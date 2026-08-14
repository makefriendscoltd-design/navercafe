"""
Telegram control panel for the YouTube -> cafe/community/script/shorts workflow.

Usage:
    python telegram_content_controller.py

The bot reads Telegram credentials from D:/coding/ccidacafe/config.ini
[COMMENT_BOT] so secrets stay out of this file.
"""

from __future__ import annotations

import configparser
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path(os.environ.get("CCIDA_CONFIG", r"D:\coding\ccidacafe\config.ini"))
STATE_PATH = ROOT / "telegram_content_controller_state.json"

YOUTUBE_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[^\s]+",
    re.I,
)
VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})")
URL_RE = re.compile(r"https?://[^\s)\]}<>\"']+")
CAFE_URL_RE = re.compile(r"https?://cafe\.naver\.com/[^\s)\]}<>\"']+", re.I)
YOUTUBE_POST_RE = re.compile(r"https?://(?:www\.)?youtube\.com/post/[^\s)\]}<>\"']+", re.I)
MP4_RE = re.compile(r"([A-Za-z]:[\\/][^\r\n]+?\.mp4)", re.I)


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    path_hints: tuple[str, ...]
    branch_hints: tuple[str, ...] = ()
    title_hints: tuple[str, ...] = ()


TARGETS = {
    "cafe": Target(
        "cafe",
        "카페글",
        ("D:/coding/ccidacafe",),
        ("feat/notebooklm-cafe-publisher",),
        ("ccidacafe",),
    ),
    "youtube": Target(
        "youtube",
        "유튜브 게시글",
        (
            "/ccidacafe/cardnews_youtube",
            "/ccidacafe/noteboolm_youtube",
        ),
        ("cardnews_youtube", "noteboolm_youtube"),
        ("cardnews_youtube", "noteboolm_youtube"),
    ),
    "script": Target(
        "script",
        "스크립트/본문",
        ("/ccidacafe/notebooklm_script",),
        ("notebooklm_script",),
        ("notebooklm_script",),
    ),
    "shorts": Target(
        "shorts",
        "쇼츠",
        ("/ccidacafe/script_video",),
        ("script_video",),
        ("script_video",),
    ),
}


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"offset": 0, "jobs": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"offset": 0, "jobs": {}}


def save_state(state: dict[str, Any]) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def load_telegram_config() -> tuple[str, str]:
    cfg = configparser.ConfigParser()
    cfg.read(DEFAULT_CONFIG, encoding="utf-8")
    section = "COMMENT_BOT"
    token = cfg.get(section, "telegram_token", fallback="").strip()
    chat_id = cfg.get(section, "telegram_chat_id", fallback="").strip()
    enabled = cfg.getboolean(section, "telegram_enabled", fallback=True)
    if not enabled:
        raise RuntimeError("telegram_enabled=false in config.ini")
    if not token or not chat_id:
        raise RuntimeError("telegram_token/telegram_chat_id missing in config.ini")
    return token, chat_id


class Telegram:
    def __init__(self, token: str, allowed_chat_id: str):
        self.token = token
        self.allowed_chat_id = str(allowed_chat_id)
        self.base = f"https://api.telegram.org/bot{token}"

    def api(self, method: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = urllib.parse.urlencode(data or {}).encode("utf-8")
        req = urllib.request.Request(f"{self.base}/{method}", data=payload)
        with urllib.request.urlopen(req, timeout=35) as res:
            return json.loads(res.read().decode("utf-8"))

    def send(self, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        data: dict[str, Any] = {
            "chat_id": self.allowed_chat_id,
            "text": text[:3900],
            "disable_web_page_preview": "true",
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        self.api("sendMessage", data)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:180]})

    def updates(self, offset: int) -> list[dict[str, Any]]:
        data = {"offset": offset, "timeout": 25, "allowed_updates": json.dumps(["message", "callback_query"])}
        return self.api("getUpdates", data).get("result", [])

    def allowed(self, chat_id: Any) -> bool:
        return str(chat_id) == self.allowed_chat_id

    def send_video_if_small(self, path: str, caption: str) -> bool:
        file_path = Path(path)
        if not file_path.exists() or file_path.stat().st_size > 48 * 1024 * 1024:
            return False
        return self._multipart("sendVideo", "video", file_path, {"chat_id": self.allowed_chat_id, "caption": caption[:900]})

    def _multipart(self, method: str, field: str, file_path: Path, fields: dict[str, str]) -> bool:
        boundary = "----ccida-telegram-boundary"
        body = bytearray()
        for key, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="{field}"; filename="{file_path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode()
        )
        body.extend(file_path.read_bytes())
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        req = urllib.request.Request(
            f"{self.base}/{method}",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as res:
                return json.loads(res.read().decode("utf-8")).get("ok", False)
        except Exception:
            return False


def run_orca(args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        ["orca", *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"orca {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout)


def list_terminals() -> list[dict[str, Any]]:
    payload = run_orca(["terminal", "list", "--json"])
    return payload.get("result", {}).get("terminals", [])


def terminal_score(term: dict[str, Any], target: Target) -> int:
    path = (term.get("worktreePath") or "").replace("\\", "/").lower()
    branch = (term.get("branch") or "").lower()
    title = (term.get("title") or "").lower()
    preview = (term.get("preview") or "").lower()
    score = 0
    if any(h.lower() in path for h in target.path_hints):
        score += 100
    if any(h.lower() in branch for h in target.branch_hints):
        score += 30
    if any(h.lower() in title or h.lower() in preview for h in target.title_hints):
        score += 10
    if term.get("connected"):
        score += 5
    if term.get("writable"):
        score += 5
    return score


def pick_terminal(target: Target, terminals: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(terminals, key=lambda t: (terminal_score(t, target), t.get("lastOutputAt") or 0), reverse=True)
    if not ranked or terminal_score(ranked[0], target) < 100:
        raise RuntimeError(f"{target.label} 워커 터미널을 찾지 못했습니다.")
    return ranked[0]


def send_prompt(handle: str, prompt: str) -> None:
    run_orca(["terminal", "send", "--terminal", handle, "--text", prompt, "--enter", "--json"])


def video_id(url: str) -> str:
    match = VIDEO_ID_RE.search(url)
    return match.group(1) if match else str(abs(hash(url)))[-8:]


def new_job_id(url: str) -> str:
    return f"{video_id(url)}-{int(time.time()) % 100000}"


def base_prompts(url: str, job_id: str) -> dict[str, str]:
    return {
        "cafe": f"""Telegram content job {job_id}
YouTube URL: {url}

Create and publish the Naver cafe article now using the existing cafe pipeline.
Hard rules:
- If cafe images are 0, do not publish.
- If the same source URL was already published, stop without publishing.
- If login is required, keep the browser visible and explicitly report LOGIN_REQUIRED.
- At the end, print exactly one marker:
  TELEGRAM_RESULT cafe status=published url=<cafe_article_url> images=<count>
  or TELEGRAM_RESULT cafe status=blocked reason=<reason>
""",
        "youtube": f"""Telegram content job {job_id}
YouTube URL: {url}

Create the NotebookLM/cardnews package and publish the YouTube community post now.
Confirm active channel is 나민수 AI before publishing.
At the end, print exactly one marker:
  TELEGRAM_RESULT youtube status=published url=<youtube_post_url> images=<count>
  or TELEGRAM_RESULT youtube status=blocked reason=<reason>
""",
        "script": f"""Telegram content job {job_id}
YouTube URL: {url}

Create script/body outputs only. Do not publish anywhere.
Save transcript, clean script, reusable body, key points, and title candidates.
At the end, print exactly one marker:
  TELEGRAM_RESULT script status=done output_dir=<absolute_path>
  or TELEGRAM_RESULT script status=blocked reason=<reason>
""",
    }


def shorts_prompt(job: dict[str, Any], regenerate: bool = False) -> str:
    url = job["url"]
    job_id = job["id"]
    result = job.get("results", {})
    script_dir = result.get("script", {}).get("output_dir", "")
    extra = "Regenerate a new shorts version; avoid repeating the previous cut/style." if regenerate else "Generate the first shorts version."
    return f"""Telegram shorts job {job_id}
Source YouTube URL: {url}
Script/body output dir: {script_dir}

{extra}
Use the existing script_video/shortform workflow in this session.
Goal:
- create one vertical YouTube Shorts-ready mp4
- propose 3 Korean YouTube Shorts titles, referencing prior title_candidates when available
- do not publish yet

At the end, print exactly one marker:
  TELEGRAM_RESULT shorts status=ready video=<absolute_mp4_path> title=<recommended_title>
  or TELEGRAM_RESULT shorts status=blocked reason=<reason>
"""


def shorts_publish_prompt(job: dict[str, Any]) -> str:
    title = job.get("shorts_title") or job.get("results", {}).get("shorts", {}).get("title") or ""
    video = job.get("results", {}).get("shorts", {}).get("video") or ""
    return f"""Telegram shorts publish job {job['id']}

Publish this video as a YouTube Shorts upload on channel 나민수 AI.
Video: {video}
Title: {title}

Before upload, confirm the title shown above is used.
After publishing, print exactly one marker:
  TELEGRAM_RESULT shorts_publish status=published url=<youtube_shorts_url>
  or TELEGRAM_RESULT shorts_publish status=blocked reason=<reason>
"""


def send_base_job(job: dict[str, Any], tg: Telegram) -> None:
    terminals = list_terminals()
    prompts = base_prompts(job["url"], job["id"])
    job["terminals"] = {}
    for key in ("cafe", "youtube", "script"):
        term = pick_terminal(TARGETS[key], terminals)
        job["terminals"][key] = term["handle"]
        send_prompt(term["handle"], prompts[key])
    job["status"] = "base_running"
    tg.send(f"접수: {job['id']}\n카페글 / 유튜브 게시글 / 스크립트 작업을 시작했습니다.")


def send_shorts_job(job: dict[str, Any], tg: Telegram, regenerate: bool = False) -> None:
    terminals = list_terminals()
    term = pick_terminal(TARGETS["shorts"], terminals)
    job.setdefault("terminals", {})["shorts"] = term["handle"]
    send_prompt(term["handle"], shorts_prompt(job, regenerate=regenerate))
    job["status"] = "shorts_running"
    tg.send(f"쇼츠 {'재생성' if regenerate else '제작'} 시작: {job['id']}")


def parse_marker(text: str) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        if "TELEGRAM_RESULT" not in line:
            continue
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        key = parts[1]
        values: dict[str, str] = {}
        for token in parts[2:]:
            if "=" in token:
                k, v = token.split("=", 1)
                values[k] = v.strip()
        found[key] = values
    return found


def infer_result(key: str, text: str) -> dict[str, str] | None:
    markers = parse_marker(text)
    if key in markers:
        return markers[key]
    if key == "cafe":
        urls = CAFE_URL_RE.findall(text)
        if urls:
            return {"status": "published", "url": urls[-1]}
    if key == "youtube":
        urls = YOUTUBE_POST_RE.findall(text)
        if urls:
            return {"status": "published", "url": urls[-1]}
    if key == "script":
        for line in text.splitlines():
            if "outputs" in line and video_id_from_path(line):
                return {"status": "done", "output_dir": line.strip()}
    if key == "shorts":
        m = MP4_RE.search(text)
        if m:
            title = ""
            title_match = re.search(r"title=([^\r\n]+)", text)
            if title_match:
                title = title_match.group(1).strip()
            return {"status": "ready", "video": m.group(1), "title": title}
    if key == "shorts_publish":
        urls = URL_RE.findall(text)
        yt = [u for u in urls if "youtube.com" in u or "youtu.be" in u]
        if yt:
            return {"status": "published", "url": yt[-1]}
    return None


def video_id_from_path(text: str) -> bool:
    return bool(re.search(r"outputs[\\/][A-Za-z0-9_-]{6,}", text))


def read_terminal(handle: str) -> str:
    payload = run_orca(["terminal", "read", "--terminal", handle, "--json"])
    tail = payload.get("result", {}).get("terminal", {}).get("tail", [])
    return "\n".join(tail)


def monitor_jobs(state: dict[str, Any], tg: Telegram) -> None:
    for job in list(state.get("jobs", {}).values()):
        if job.get("status") in ("done", "failed"):
            continue
        results = job.setdefault("results", {})
        terminals = job.get("terminals", {})
        for key, handle in terminals.items():
            if key in results and results[key].get("status") not in ("running", ""):
                continue
            try:
                text = read_terminal(handle)
            except Exception as e:
                results[key] = {"status": "blocked", "reason": f"terminal_read_failed:{e}"}
                continue
            parsed = infer_result(key, text)
            if parsed:
                results[key] = parsed

        if job.get("status") == "base_running" and all(k in results for k in ("cafe", "youtube", "script")):
            send_base_summary(job, tg)
            send_shorts_job(job, tg)

        if job.get("status") == "shorts_running" and "shorts" in results:
            send_shorts_summary(job, tg)
            job["status"] = "shorts_ready"

        if job.get("status") == "shorts_publish_running" and "shorts_publish" in results:
            r = results["shorts_publish"]
            if r.get("status") == "published":
                tg.send(f"쇼츠 발행 완료: {job['id']}\n{r.get('url', '')}")
                job["status"] = "done"
            else:
                tg.send(f"쇼츠 발행 실패/보류: {job['id']}\n{r.get('reason', r)}")
                job["status"] = "shorts_ready"


def send_base_summary(job: dict[str, Any], tg: Telegram) -> None:
    r = job["results"]
    lines = [f"기본 발행 결과: {job['id']}"]
    cafe = r.get("cafe", {})
    yt = r.get("youtube", {})
    script = r.get("script", {})
    lines.append(f"카페글: {cafe.get('status')} {cafe.get('url', cafe.get('reason', ''))}")
    lines.append(f"유튜브 게시글: {yt.get('status')} {yt.get('url', yt.get('reason', ''))}")
    lines.append(f"본문/스크립트: {script.get('status')} {script.get('output_dir', script.get('reason', ''))}")
    tg.send("\n".join(lines))


def send_shorts_summary(job: dict[str, Any], tg: Telegram) -> None:
    r = job["results"].get("shorts", {})
    video = r.get("video", "")
    title = r.get("title", "") or "제목 미확정"
    job["shorts_title"] = title
    text = f"쇼츠 제작 완료: {job['id']}\n제목: {title}\n파일: {video}\n\n/title {job['id']} 새 제목\n으로 제목 수정 가능"
    buttons = {
        "inline_keyboard": [
            [{"text": "쇼츠 발행", "callback_data": f"publish:{job['id']}"}],
            [{"text": "쇼츠 재생성", "callback_data": f"regen:{job['id']}"}],
        ]
    }
    if video and tg.send_video_if_small(video, text):
        tg.send("위 쇼츠를 발행하거나 재생성할 수 있습니다.", buttons)
    else:
        tg.send(text, buttons)


def handle_message(msg: dict[str, Any], state: dict[str, Any], tg: Telegram) -> None:
    chat_id = msg.get("chat", {}).get("id")
    if not tg.allowed(chat_id):
        return
    text = msg.get("text") or ""
    if text.startswith("/status"):
        lines = ["작업 상태"]
        for job in state.get("jobs", {}).values():
            lines.append(f"{job['id']}: {job.get('status')} {job.get('url')}")
        tg.send("\n".join(lines) if len(lines) > 1 else "진행 중인 작업 없음")
        return
    if text.startswith("/title"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            tg.send("/title <job_id> <제목> 형식으로 보내주세요.")
            return
        job = state.get("jobs", {}).get(parts[1])
        if not job:
            tg.send("해당 job_id를 찾지 못했습니다.")
            return
        job["shorts_title"] = parts[2].strip()
        tg.send(f"쇼츠 제목 수정됨: {job['id']}\n{job['shorts_title']}")
        return
    match = YOUTUBE_RE.search(text)
    if not match:
        tg.send("유튜브 링크를 보내면 카페글/유튜브 게시글/본문 생성부터 시작합니다.\n/status 로 상태 확인")
        return
    url = match.group(0)
    job_id = new_job_id(url)
    job = {"id": job_id, "url": url, "status": "queued", "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    state.setdefault("jobs", {})[job_id] = job
    try:
        send_base_job(job, tg)
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        tg.send(f"작업 시작 실패: {job_id}\n{e}")


def handle_callback(cb: dict[str, Any], state: dict[str, Any], tg: Telegram) -> None:
    callback_id = cb.get("id", "")
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    if not tg.allowed(chat_id):
        return
    data = cb.get("data", "")
    action, _, job_id = data.partition(":")
    job = state.get("jobs", {}).get(job_id)
    if not job:
        tg.answer_callback(callback_id, "job not found")
        return
    try:
        if action == "regen":
            tg.answer_callback(callback_id, "쇼츠 재생성 시작")
            job.get("results", {}).pop("shorts", None)
            send_shorts_job(job, tg, regenerate=True)
        elif action == "publish":
            tg.answer_callback(callback_id, "쇼츠 발행 시작")
            terminals = list_terminals()
            term = pick_terminal(TARGETS["shorts"], terminals)
            job.setdefault("terminals", {})["shorts_publish"] = term["handle"]
            send_prompt(term["handle"], shorts_publish_prompt(job))
            job["status"] = "shorts_publish_running"
            tg.send(f"쇼츠 발행 요청됨: {job_id}\n제목: {job.get('shorts_title', '')}")
    except Exception as e:
        tg.send(f"버튼 처리 실패: {job_id}\n{e}")


def main() -> int:
    token, chat_id = load_telegram_config()
    tg = Telegram(token, chat_id)
    state = load_state()
    tg.send("콘텐츠 컨트롤러 시작됨. 유튜브 링크를 보내세요.")
    while True:
        try:
            updates = tg.updates(int(state.get("offset", 0)))
            for upd in updates:
                state["offset"] = max(int(state.get("offset", 0)), int(upd["update_id"]) + 1)
                if "message" in upd:
                    handle_message(upd["message"], state, tg)
                elif "callback_query" in upd:
                    handle_callback(upd["callback_query"], state, tg)
            monitor_jobs(state, tg)
            save_state(state)
        except KeyboardInterrupt:
            return 0
        except urllib.error.HTTPError as e:
            if e.code == 409:
                print(
                    "[telegram] 409 Conflict: another getUpdates poller is using this bot token. "
                    "Stop the other bot process or use a separate Telegram bot token.",
                    file=sys.stderr,
                )
                return 3
            print(f"[telegram] http error: {e}", file=sys.stderr)
            time.sleep(10)
        except urllib.error.URLError as e:
            print(f"[telegram] network error: {e}", file=sys.stderr)
            time.sleep(10)
        except Exception as e:
            print(f"[controller] error: {e}", file=sys.stderr)
            time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())
