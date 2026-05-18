"""
네이버 카페 자동 포스팅 — 웹 서버
====================================
FastAPI 기반 로컬 웹 인터페이스.
브라우저에서 http://localhost:8000 으로 접속하여 사용합니다.

실행: python server.py
의존성: pip install fastapi uvicorn python-multipart
"""

import os
import sys
import uuid
import json
import queue
import shutil
import asyncio
import threading
import traceback
import configparser
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(SCRIPT_DIR, "uploads")
STATIC_DIR = os.path.join(SCRIPT_DIR, "static")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.ini")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ── 태스크 상태 관리 ──────────────────────────────────────────────────

class TaskState:
    def __init__(self):
        self.log_queue: queue.Queue = queue.Queue()
        self.status = "running"      # running | preview | posting | done | error | cancelled
        self.title = ""
        self.body = ""
        self.threads_post = ""
        self.image_paths: list = []
        self.preview_event = threading.Event()
        self.preview_confirmed = False
        self.preview_title = ""
        self.preview_body = ""


TASKS: dict[str, TaskState] = {}

# ── FastAPI 앱 ────────────────────────────────────────────────────────

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ── 자동화 실행 스레드 ────────────────────────────────────────────────

def _run(task_id: str, source: str, optional_config: dict, image_paths: list):
    state = TASKS[task_id]

    class _LogWriter:
        def write(self, msg):
            if msg and msg.strip():
                state.log_queue.put({"type": "log", "msg": msg.rstrip()})
            return len(msg) if msg else 0
        def flush(self): pass

    old_out, old_err = sys.stdout, sys.stderr
    lw = _LogWriter()
    sys.stdout = lw
    sys.stderr = lw

    try:
        import youtube_cafe_auto as auto

        auto.NAVER_ID      = optional_config.get('naver_id', '')
        auto.NAVER_PW      = optional_config.get('naver_pw', '')
        auto.CAFE_URL      = optional_config.get('cafe_url', '')
        auto.GEMINI_API_KEY = optional_config.get('gemini_key', '')

        source_type, norm = auto.detect_source_type(source)
        labels = {'youtube': '유튜브 영상', 'article': '웹 기사/페이지', 'text': '직접 텍스트'}
        state.log_queue.put({"type": "log", "msg": f"[소스] {labels.get(source_type, source_type)}"})

        source_text = None
        ic = optional_config.get('image_count', 6)

        if source_type == 'youtube':
            vid = auto.extract_video_id(norm)
            if not vid:
                state.log_queue.put({"type": "error", "msg": "[오류] 유효한 유튜브 URL이 아닙니다."})
                state.status = "error"
                return
            source_text = auto.get_transcript(vid)
            if not source_text:
                state.log_queue.put({"type": "error", "msg": "[오류] 자막을 추출할 수 없습니다."})
                state.status = "error"
                return
            if not image_paths:
                image_paths = auto.extract_frames(norm, ic)

        elif source_type == 'article':
            source_text = auto.scrape_article(norm)
            if not source_text:
                state.log_queue.put({"type": "error", "msg": "[오류] 웹 페이지 텍스트 추출 실패."})
                state.status = "error"
                return

        else:
            source_text = norm

        # AI 글 생성
        has_img = len(image_paths) > 0
        title, body = auto.generate_blog_post(
            source_text, source_type, has_img, optional_config, len(image_paths))

        if has_img and "[IMAGE_HERE]" not in body:
            state.log_queue.put({"type": "log", "msg": "[경고] [IMAGE_HERE] 마커 없음."})

        threads_post = auto.generate_threads_post(source_text)

        state.title = title
        state.body = body
        state.threads_post = threads_post
        state.image_paths = image_paths
        state.status = "preview"
        state.log_queue.put({"type": "preview_ready", "msg": "[완료] 글 생성 완료. 미리보기를 확인하세요."})

        # 사용자 확인 대기
        state.preview_event.wait()

        if not state.preview_confirmed:
            state.log_queue.put({"type": "log", "msg": "[취소] 게시가 취소되었습니다."})
            state.status = "cancelled"
            state.log_queue.put({"type": "done", "msg": ""})
            return

        state.status = "posting"
        state.log_queue.put({"type": "log", "msg": "[시작] 네이버 카페 게시 중..."})

        final_title = state.preview_title or title
        final_body  = state.preview_body  or body

        auto.post_to_naver_cafe(final_title, final_body, image_paths, optional_config)
        auto.cleanup_temp_files()

        state.status = "done"
        state.log_queue.put({"type": "done", "msg": "[완료] 카페에 글이 등록되었습니다!"})

    except Exception as e:
        state.log_queue.put({"type": "error", "msg": f"[오류] {e}\n{traceback.format_exc()}"})
        state.status = "error"
        state.log_queue.put({"type": "done", "msg": ""})

    finally:
        sys.stdout = old_out
        sys.stderr = old_err
        # 업로드 임시 파일 삭제
        for p in image_paths:
            if UPLOAD_DIR in p:
                try: os.remove(p)
                except: pass


# ── 라우트 ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/api/config")
async def get_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    cfg = configparser.RawConfigParser()
    cfg.read(CONFIG_FILE, encoding="utf-8")
    out = {}
    for sec in cfg.sections():
        for k, v in cfg.items(sec):
            out[f"{sec.lower()}_{k}"] = v
    return out


@app.post("/api/config")
async def save_config(request: Request):
    data = await request.json()
    cfg = configparser.RawConfigParser()
    if os.path.exists(CONFIG_FILE):
        cfg.read(CONFIG_FILE, encoding="utf-8")

    mapping = {
        'naver_id':                    ('NAVER',      'id'),
        'naver_pw':                    ('NAVER',      'pw'),
        'naver_cafe_url':              ('NAVER',      'cafe_url'),
        'gemini_api_key':              ('GEMINI',     'api_key'),
        'cta_enabled':                 ('CTA',        'enabled'),
        'cta_text':                    ('CTA',        'text'),
        'cta_link_url':                ('CTA',        'link_url'),
        'cta_link_text':               ('CTA',        'link_text'),
        'formatting_bold_enabled':     ('FORMATTING', 'bold_enabled'),
        'formatting_highlight_enabled':('FORMATTING', 'highlight_enabled'),
        'formatting_highlight_color':  ('FORMATTING', 'highlight_color'),
        'prompt_custom_instructions':  ('PROMPT',     'custom_instructions'),
        'content_min_length':          ('CONTENT',    'min_length'),
        'content_max_length':          ('CONTENT',    'max_length'),
        'content_max_paragraphs':      ('CONTENT',    'max_paragraphs'),
        'content_image_count':         ('CONTENT',    'image_count'),
    }
    for key, val in data.items():
        if key in mapping:
            sec, opt = mapping[key]
            if not cfg.has_section(sec):
                cfg.add_section(sec)
            cfg.set(sec, opt, str(val))

    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        cfg.write(f)
    return {"ok": True}


@app.post("/api/run")
async def run_task(
    source:              str  = Form(...),
    naver_id:            str  = Form(""),
    naver_pw:            str  = Form(""),
    cafe_url:            str  = Form(""),
    gemini_key:          str  = Form(""),
    cta_enabled:         str  = Form("false"),
    cta_text:            str  = Form(""),
    cta_link_url:        str  = Form(""),
    cta_link_text:       str  = Form(""),
    bold_enabled:        str  = Form("true"),
    highlight_enabled:   str  = Form("true"),
    highlight_color:     str  = Form("#FFFF00"),
    custom_instructions: str  = Form(""),
    min_length:          int  = Form(1500),
    max_length:          int  = Form(2500),
    max_paragraphs:      int  = Form(6),
    image_count:         int  = Form(6),
    images: Optional[list[UploadFile]] = File(None),
):
    task_id = str(uuid.uuid4())
    state = TaskState()
    TASKS[task_id] = state

    # 이미지 저장
    saved_paths = []
    if images:
        for img in images:
            if img.filename:
                dest = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}_{img.filename}")
                with open(dest, "wb") as f:
                    shutil.copyfileobj(img.file, f)
                saved_paths.append(dest)

    optional_config = {
        'naver_id':          naver_id,
        'naver_pw':          naver_pw,
        'cafe_url':          cafe_url,
        'gemini_key':        gemini_key,
        'cta_enabled':       cta_enabled.lower() == "true",
        'cta_text':          cta_text,
        'cta_link_url':      cta_link_url,
        'cta_link_text':     cta_link_text,
        'bold_enabled':      bold_enabled.lower() == "true",
        'highlight_enabled': highlight_enabled.lower() == "true",
        'highlight_color':   highlight_color,
        'custom_instructions': custom_instructions,
        'min_length':        min_length,
        'max_length':        max_length,
        'max_paragraphs':    max_paragraphs,
        'image_count':       image_count,
    }

    threading.Thread(target=_run,
                     args=(task_id, source, optional_config, saved_paths),
                     daemon=True).start()

    return {"task_id": task_id}


@app.get("/api/logs/{task_id}")
async def stream_logs(task_id: str, request: Request):
    if task_id not in TASKS:
        return JSONResponse({"error": "not found"}, status_code=404)
    state = TASKS[task_id]

    async def gen():
        while True:
            if await request.is_disconnected():
                break
            try:
                item = state.log_queue.get(timeout=0.3)
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                if item.get("type") == "done":
                    break
            except queue.Empty:
                yield ": ping\n\n"
                await asyncio.sleep(0.1)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/preview/{task_id}")
async def get_preview(task_id: str):
    if task_id not in TASKS:
        return JSONResponse({"error": "not found"}, status_code=404)
    s = TASKS[task_id]
    return {"status": s.status, "title": s.title,
            "body": s.body, "threads_post": s.threads_post}


@app.post("/api/confirm/{task_id}")
async def confirm(task_id: str, request: Request):
    if task_id not in TASKS:
        return JSONResponse({"error": "not found"}, status_code=404)
    data = await request.json()
    s = TASKS[task_id]
    s.preview_confirmed = data.get("proceed", False)
    s.preview_title = data.get("title", s.title)
    s.preview_body  = data.get("body",  s.body)
    s.preview_event.set()
    return {"ok": True}


# ── 진입점 ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import webbrowser
    url = "http://localhost:8000"
    print(f"서버 시작: {url}")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
