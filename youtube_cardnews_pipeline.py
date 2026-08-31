# -*- coding: utf-8 -*-
"""
YouTube link -> NotebookLM cafe manuscript -> cardnews PNGs -> YouTube post text.

Defaults are conservative:
  - generates local outputs
  - creates a Naver Cafe draft only when --cafe-draft is passed
  - does not publish public YouTube posts automatically
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path

import requests

import notebooklm_source as nlm
import notebooklm_shorts as shorts
import youtube_cafe_auto as auto
from notebook_cafe_auto import build_body, count_sections, make_title
from youtube_community_auto import post_to_youtube_community
from aside_browser import (
    aside_available,
    capture_youtube_frames as capture_youtube_frames_aside,
    upload_youtube_short,
)
from external_publish_tracking import track_publication
from telegram_publish_approval import request_publish_approval
from content_factcheck import factcheck_manuscript
from shorts_video import (
    normalize_shorts_title,
    render_short_video,
    validate_minsoo_voice_artifact,
    validate_short_video,
)


SCRIPT_DIR = Path(__file__).resolve().parent
_MIGRATED_CARDNEWS_ROOT = Path.home() / "orca" / "projects" / "cardnews"
CARDNEWS_ROOT = Path(os.environ.get("CARDNEWS_ROOT", str(_MIGRATED_CARDNEWS_ROOT)))
CARDNEWS_PORT = int(os.environ.get("CARDNEWS_PORT", "8787"))
OUTPUT_ROOT = SCRIPT_DIR / "outputs"
FIRST_PUBLISH_STATE_FILE = SCRIPT_DIR / ".content_publish_state.json"
YOUTUBE_FINAL_LINE = "AI 자동화에 관심 있는 분들을 위한 커뮤니티를 운영중입니다. 댓글에 aimax 남겨주세요. 관련 정보 보내드릴게요."
CARD_PORTRAIT_SIZE = (1080, 1350)
CARD_SQUARE_SIZE = (1080, 1080)

YOUTUBE_POST_PROMPT = """아래 원문을 나민수 AI 유튜브 커뮤니티 게시글 스타일로 다시 써라.

최근 게시글의 스타일 기준:
- 첫 줄은 반드시 대괄호 후킹으로 시작한다.
  예: [콘텐츠 아이디어가 떨어진다면] AI로 절대 마르지 않는 아이디어 시스템을 만드는 법을 정리했습니다.
  예: [1인 기업이 월 5억 매출을 만드는 법] Claude Code 마케팅 자동화 4단계를 정리했습니다.
- 첫 줄은 짧은 제목이 아니라, 독자가 얻을 결과와 정리 개수를 함께 말한다.
- 도입부는 4~7문장으로 쓴다. 문제 상황, 변화, 기회, 핵심 주장을 차례로 설명한다.
- 도입부 마지막 문장은 항상 "핵심 내용을 정리했습니다." 또는 "3가지를 정리했습니다."처럼 본문으로 연결한다.

본문 형식:
- 섹션 구분은 반드시 "----" 한 줄로 한다.
- 본문은 반드시 4~5개 섹션으로 구성한다.
- 원문이 짧아도 관점을 나누어 최소 4개 섹션까지 확장한다.
- 각 섹션 제목은 "1. 먼저 비즈니스의 병목 지점을 찾으세요"처럼 번호 + 실행형 제목으로 쓴다.
- 섹션 번호는 반드시 1부터 순서대로 붙이고, 결론에는 번호를 붙이지 않는다.
- 각 섹션은 1개의 긴 설명 단락 또는 2개의 중간 단락으로 쓴다.
- 너무 짧은 카드뉴스 문장처럼 끊지 말고, 유튜브 커뮤니티 긴 글처럼 정보 밀도를 높인다.
- 표, 목록, 불릿, 마크다운, 굵게 표시, 별표 강조를 절대 쓰지 않는다.
- "**", "__", "###", "- " 같은 AI 생성문 특유의 서식 흔적을 출력하지 않는다.
- 원문에 있는 구체적 숫자, 제품명, 회사명, 도구명, 사례는 최대한 살린다.
- 원문에 없는 사실은 만들지 않는다.

문체:
- 한국어 구어체지만 지나치게 가볍게 쓰지 않는다.
- "했어요"보다 "했습니다", "됩니다", "보입니다"를 기본으로 쓴다.
- 과장 광고처럼 쓰지 말고, 실무자가 읽는 설명형 글처럼 쓴다.
- 전문 용어는 필요할 때만 원어를 괄호로 병기한다. 예: 워크플로(Workflow), 입력(Input)
- 한 문장은 너무 짧게 쪼개지 말고, 의미 단위로 자연스럽게 이어 쓴다.

결론 형식:
- 마지막 구분선 뒤에는 반드시 "결론"으로 시작한다.
- 결론은 3~5문장으로 핵심 메시지를 다시 압축한다.
- 마지막에서 두 번째 문장은 독자가 바로 실행할 행동을 제안한다.
- 마지막 문장은 반드시 아래 문장 그대로 끝낸다.
AI 자동화에 관심 있는 분들을 위한 커뮤니티를 운영중입니다. 댓글에 aimax 남겨주세요. 관련 정보 보내드릴게요.

출력 규칙:
- 게시글 본문만 출력한다.
- 코드블록, 설명, 따옴표, 제목 라벨을 붙이지 않는다.

원문:
{article}
"""


CARD_DECK_EXTRA_RULES = """

추가 카드뉴스 규칙:
- 정확히 10장의 slides를 만들어라.
- preset은 반드시 "cine"로 고정한다.
- 각 카드의 f.bgByStyle.cine 값을 서로 다르게 지정해 카드별 배경 변화가 보이게 하라.
- 사용할 수 있는 배경은 assets/cover-a.png, assets/cover-b.png, assets/shaft.png, assets/sunrise.png, assets/streaks.png, assets/cine-1.png, assets/cine-2.png, assets/cine-3.png, assets/cine-4.png 이다.
- 마지막 10번째 카드는 closing 카드다.
- 카드 문구는 한국어로 자연스럽게 작성한다.
- 카드 문구는 원문을 길게 잘라 붙이지 말고, 사람이 카드뉴스용으로 다시 쓴 짧은 카피처럼 작성한다.
- head는 12~24자 안팎의 명령형/판단형 문장으로 쓰고, desc는 1개의 완성된 문장으로 쓴다.
- desc는 55자 이내로 작성하고 문장 중간에서 끊기지 않게 한다.
- head와 desc가 같은 문장이면 안 된다.
- "핵심", "최적화", "극대화", "강력한", "상상 이상", "거인의 어깨"처럼 흔한 AI 요약문 느낌의 표현을 남발하지 않는다.
- "ChatG", "NotebookLM은"처럼 단어 또는 문장이 중간에 끊긴 결과를 절대 출력하지 않는다.
- 마크다운 기호(##, **, -, 숫자 목록 표기)를 카드 안에 넣지 않는다.
"""

CARD_BG_POOL = [
    "assets/cover-a.png",
    "assets/cover-b.png",
    "assets/shaft.png",
    "assets/sunrise.png",
    "assets/streaks.png",
    "assets/cine-1.png",
    "assets/cine-2.png",
    "assets/cine-3.png",
    "assets/cine-4.png",
]

BANNED_CARD_PHRASES = (
    "거인의 어깨",
    "극대화",
    "상상 이상",
    "강력한",
)

SQUARE_CARD_CSS = """
.card{width:1080px!important;height:1080px!important;}
.cover{padding:76px 92px!important;}
.cover.bottom{gap:30px!important;}
.cover .title{font-size:86px!important;line-height:1.08!important;letter-spacing:-.04em!important;}
.cover:not(.bottom) .title{margin-top:28px!important;}
.cover .sub{font-size:32px!important;line-height:1.42!important;max-width:840px!important;}
.badge{font-size:24px!important;padding:12px 26px!important;}
.foot .brand{font-size:28px!important;}.foot .swipe{font-size:28px!important;}
.content{padding:70px 92px!important;}
.content .top{padding-bottom:24px!important;}
.content .body{gap:28px!important;}
.content .bignum{font-size:118px!important;}
.content .head{font-size:68px!important;line-height:1.12!important;}
.content .desc{font-size:33px!important;line-height:1.42!important;max-width:880px!important;}
.content .foot .brand{font-size:25px!important}.content .foot .swipe{font-size:26px!important}
.closing{padding:78px 92px!important;}
.closing .mid{gap:32px!important;}
.closing.bottom .mid{padding-bottom:18px!important;}
.closing .head{font-size:76px!important;line-height:1.1!important;}
.closing .desc{font-size:34px!important;line-height:1.38!important;}
.pill{font-size:28px!important;padding:20px 36px!important;}
.closing .foot{padding-top:24px!important;}.closing .brand{font-size:31px!important}.closing .hint{font-size:24px!important;}
.quote{padding:86px 92px!important;gap:34px!important;}
.quote.bottom{padding-bottom:110px!important;}
.quote .qtext{font-size:64px!important;}
.quote .qmark{font-size:210px!important;height:104px!important;}
.table{padding:76px 84px!important;gap:34px!important;}
.table .ttl{font-size:60px!important}
.row>div{padding:24px 22px!important;font-size:27px!important;}
"""


def slug(text):
    text = re.sub(r"https?://", "", text or "")
    text = re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", text).strip("-")
    return (text[:50] or f"run-{int(time.time())}")


def load_runtime_config():
    config = auto.load_or_create_config()
    auto.NAVER_ID = config["NAVER"]["id"]
    auto.NAVER_PW = config["NAVER"]["pw"]
    auto.CAFE_URL = config["NAVER"]["cafe_url"]
    auto.GEMINI_API_KEY = config["GEMINI"]["api_key"]
    return config, auto.load_optional_config(config), nlm.load_config(config)


def read_text_file(path):
    return Path(path).read_text(encoding="utf-8").strip()


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_first_publish_state():
    try:
        data = json.loads(FIRST_PUBLISH_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_first_publish_state(state):
    tmp = FIRST_PUBLISH_STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, FIRST_PUBLISH_STATE_FILE)


def save_bundle_publish_status(out_dir, state):
    """Atomically persist the three-channel result for resumable retries."""
    path = Path(out_dir) / "publish_status.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_bundle_publish_status(out_dir):
    path = Path(out_dir) / "publish_status.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def three_channel_complete(state):
    """Require provider proof for all three channels from the same source."""
    source_id = str(state.get("source_id") or "").strip()
    if not source_id:
        return False
    channels = (
        ("cafe_published", "cafe_source_id", "cafe_url"),
        ("youtube_published", "youtube_source_id", "youtube_url"),
        ("shorts_published", "shorts_source_id", "shorts_url"),
    )
    return all(
        bool(state.get(published))
        and str(state.get(source_key) or "").strip() == source_id
        and bool(str(state.get(provider_url) or "").strip())
        for published, source_key, provider_url in channels
    )


def publish_fingerprint(source_url, title, cafe_body, youtube_body, shorts_body=""):
    source_identity = auto._publish_source_key(source_url) or (source_url or "")
    raw = "\n\0\n".join(
        (source_identity, title or "", cafe_body or "", youtube_body or "", shorts_body or "")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run_claude(prompt, timeout=240):
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI를 찾을 수 없습니다.")
    r = subprocess.run(
        [exe, "-p", "--output-format", "text"],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "claude 실행 실패").strip())
    return r.stdout.strip()


def extract_json(text):
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("JSON 객체를 찾지 못했습니다.")
    return json.loads(text[start:end + 1])


def make_youtube_post(manuscript):
    prompt = YOUTUBE_POST_PROMPT.format(article=manuscript)
    try:
        from google import genai
        with genai.Client(api_key=auto.GEMINI_API_KEY) as client:
            resp = client.models.generate_content(
                model=os.environ.get("YOUTUBE_POST_MODEL", "gemini-2.5-flash"),
                contents=prompt,
            )
        text = (resp.text or "").strip()
        if text:
            return sanitize_youtube_post(text)
    except Exception as e:
        print(f"[주의] Gemini 유튜브 게시글 변환 실패, Claude로 대체합니다: {e}")
    try:
        return sanitize_youtube_post(run_claude(prompt, timeout=240))
    except Exception as e:
        print(f"[주의] Claude 유튜브 게시글 변환 실패, 로컬 형식 변환으로 대체합니다: {e}")
        return sanitize_youtube_post(local_youtube_post(manuscript))


def local_youtube_post(manuscript):
    text = re.sub(r"\r\n?", "\n", manuscript or "").strip()
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    heading = ""
    sections = []
    current = None

    for block in blocks:
        if block.startswith("## "):
            title = re.sub(r"^\s*##\s*", "", block).strip()
            if not heading:
                heading = title
            else:
                if current:
                    sections.append(current)
                current = {"title": title, "body": []}
        elif current:
            current["body"].append(block)
        elif not heading:
            heading = re.sub(r"^\s{0,3}#{1,6}\s*", "", block.splitlines()[0]).strip()
        else:
            current = {"title": heading, "body": [block]}
    if current:
        sections.append(current)

    if not heading:
        heading = "AI 광고 자동화의 변화"
    if not sections:
        sections = [{"title": "핵심 내용을 확인하세요", "body": blocks[:2] or [text]}]

    intro_body = " ".join(sections[0].get("body") or [])
    intro_sentences = re.findall(r".+?(?:다\.|[.!?。]|$)", re.sub(r"\s+", " ", intro_body))
    intro = " ".join([s.strip() for s in intro_sentences if s.strip()][:4])
    if not intro:
        intro = re.sub(r"\s+", " ", text)[:450]

    out = [f"[{heading}] AI 광고 자동화에서 확인해야 할 핵심 변화를 정리했습니다.", ""]
    out.append(intro)
    out.append("이번 영상에서 다룬 핵심 내용을 정리했습니다.")

    for idx, section in enumerate(sections[:5], 1):
        title = re.sub(r"^\s*\d+[.)]\s*", "", section["title"]).strip()
        body = "\n\n".join(section.get("body") or [])
        body = re.sub(r"^\s{0,3}#{1,6}\s*", "", body, flags=re.MULTILINE)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        if not body:
            continue
        out.extend(["", "----", "", f"{idx}. {title}", "", body])

    out.extend([
        "",
        "----",
        "",
        "결론",
        f"{heading}의 핵심은 AI가 단순한 보조 도구를 넘어 실제 업무 흐름을 직접 연결하는 단계로 이동하고 있다는 점입니다.",
        "지금은 도구 이름보다 어떤 데이터를 넣고, 어떤 판단을 맡기고, 어디까지 자동화할지 정하는 설계가 더 중요합니다.",
        "오늘 업무에서 반복되는 광고 분석, 소재 생성, 게시 과정을 하나 선택해 자동화 흐름으로 정리해 보세요.",
        YOUTUBE_FINAL_LINE,
    ])
    return "\n".join(out)


def sanitize_youtube_post(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(
        r"(?:이\s*)?글이\s+도움이\s+됐(?:다|다면).*?(?:감사합니다\.?)?\s*$",
        YOUTUBE_FINAL_LINE,
        text,
        flags=re.DOTALL,
    )
    if not text.endswith(YOUTUBE_FINAL_LINE):
        text = text.rstrip() + "\n" + YOUTUBE_FINAL_LINE
    text = re.sub(rf"\s*{re.escape(YOUTUBE_FINAL_LINE)}$", f"\n{YOUTUBE_FINAL_LINE}", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_card_text(text, max_len=None):
    text = (text or "").strip()
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    if max_len and len(text) > max_len:
        text = truncate_card_text(text, max_len)
    return text


def is_card_slop(text):
    text = text or ""
    return any(phrase in text for phrase in BANNED_CARD_PHRASES)


def truncate_card_text(text, max_len):
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rstrip()
    first_sentence = first_card_sentence(text)
    if 8 <= len(first_sentence) <= max_len:
        return first_sentence
    for sep in (" ", ",", "，"):
        if sep in cut:
            candidate = cut.rsplit(sep, 1)[0].strip()
            if len(candidate) >= 8:
                return candidate
    return cut


def first_card_sentence(text):
    text = (text or "").strip()
    if not text:
        return ""
    stops = [". ", "? ", "! ", "。", "다. ", "요. "]
    end = None
    for stop in stops:
        pos = text.find(stop)
        if pos >= 8:
            candidate_end = pos + len(stop.strip())
            end = candidate_end if end is None else min(end, candidate_end)
    if end is not None:
        return text[:end].strip()
    return text.strip()


def manuscript_card_points(manuscript, limit=8):
    chunks = []
    for block in re.split(r"(?m)^##\s+", manuscript or ""):
        block = block.strip()
        if not block:
            continue
        lines = [line.strip() for line in block.splitlines() if line.strip() and line.strip() != "----"]
        if not lines:
            continue
        title = clean_card_text(lines[0], 24)
        body = clean_card_text(" ".join(lines[1:]))
        if not body:
            continue
        sentences = [clean_card_text(s) for s in body.split(".") if len(clean_card_text(s)) > 12]
        for n, sentence in enumerate(sentences[:2]):
            desc = clean_card_text(sentence + ".", 58)
            if is_card_slop(desc):
                continue
            head = title
            if head and desc and head != desc:
                chunks.append((head, desc))
            if len(chunks) >= limit:
                break
        if len(chunks) >= limit:
            break
    if chunks:
        return chunks[:limit]

    paragraphs = [clean_card_text(c) for c in re.split(r"\n\s*\n", manuscript or "") if clean_card_text(c)]
    out = []
    for para in paragraphs:
        head = clean_card_text(para, 24)
        desc = clean_card_text(para, 58)
        if head and desc and head != desc:
            out.append((head, desc))
        if len(out) >= limit:
            break
    return out


def fallback_deck(manuscript, title):
    points = manuscript_card_points(manuscript, limit=8)
    def bg(i):
        return {"bgByStyle": {"cine": CARD_BG_POOL[i % len(CARD_BG_POOL)]}}

    slides = [
        {"type": "cover", "f": {"badge": "AIMAX", "title": clean_card_text(title, 38), "sub": clean_card_text(points[0][0] if points else "", 80), **bg(0)}}
    ]
    for idx in range(1, 9):
        head, desc = points[idx - 1] if idx - 1 < len(points) else ("다음 행동을 정한다", "계획을 문서로 끝내지 말고 바로 실행 가능한 작업으로 바꿉니다.")
        slides.append({
            "type": "content",
            "f": {"idx": f"{idx:02d}", "total": "08", "tag": "Insight", "num": f"{idx:02d}", "head": head, "desc": desc, **bg(idx)},
        })
    slides.append({
        "type": "closing",
        "f": {"head": "AI 커뮤니티에\n함께해요", "desc": "참여하고 싶다면 댓글에\nAIMAX를 남겨주세요", "cta1": "댓글 AIMAX", "cta2": "바로 초대", "hint": "커뮤니티 초대를 보내드릴게요", **bg(9)},
    })
    return {"handle": "@aimax", "preset": "cine", "accent": None, "slides": slides}


def deck_validation_issues(deck, wanted=10):
    issues = []
    slides = list((deck or {}).get("slides") or [])
    if len(slides) != wanted:
        issues.append(f"카드 수 {len(slides)}장")
        return issues
    if slides[0].get("type") != "cover":
        issues.append("첫 카드가 cover가 아님")
    if slides[-1].get("type") != "closing":
        issues.append("마지막 카드가 closing이 아님")
    if not str((slides[0].get("f") or {}).get("title") or "").strip():
        issues.append("표지 제목이 비어 있음")
    middle = slides[1:-1]
    ideas, descriptions = [], []
    for index, slide in enumerate(middle, 2):
        fields = slide.get("f") or {}
        slide_type = slide.get("type")
        if slide_type == "content":
            head = str(fields.get("head") or "").strip()
            desc = str(fields.get("desc") or "").strip()
            ideas.append(head)
            descriptions.append(desc)
            if not (6 <= len(head) <= 34):
                issues.append(f"{index}번 카드 제목 길이")
            complete = bool(re.search(r"(?:[.!?。]|다|요|함|됨|임|음)$", desc))
            if not (12 <= len(desc) <= 70) or not complete:
                issues.append(f"{index}번 카드 설명이 잘렸거나 문장이 아님")
            if head == desc:
                issues.append(f"{index}번 카드 제목·설명 중복")
            check_text = f"{head} {desc}"
        elif slide_type == "quote":
            quote = str(fields.get("quote") or "").strip()
            ideas.append(quote)
            if not (12 <= len(quote) <= 70):
                issues.append(f"{index}번 인용 카드 문구 길이")
            check_text = quote
        elif slide_type == "table":
            title = str(fields.get("title") or "").strip()
            rows = [row.strip() for row in str(fields.get("rows") or "").splitlines() if row.strip()]
            ideas.append(title)
            if not (6 <= len(title) <= 34):
                issues.append(f"{index}번 표 카드 제목 길이")
            if len(rows) < 2 or any(len([cell for cell in row.split("|") if cell.strip()]) != 3 for row in rows):
                issues.append(f"{index}번 표 카드 행 형식")
            check_text = f"{title} {' '.join(rows)}"
        else:
            issues.append(f"{index}번 카드 지원하지 않는 형식")
            check_text = ""
        if is_card_slop(check_text):
            issues.append(f"{index}번 카드 금지 표현")
        if re.search(r"(?:__|^#{1,6}\s|^[-*]\s)", check_text, re.M):
            issues.append(f"{index}번 카드 마크다운 잔존")
    if len(set(ideas)) != len(ideas):
        issues.append("본문 카드 아이디어 중복")
    if len(set(descriptions)) != len(descriptions):
        issues.append("본문 카드 설명 중복")
    return issues


def _gemini_card_deck(prompt):
    from google import genai
    from google.genai import types

    with genai.Client(api_key=auto.GEMINI_API_KEY) as client:
        response = client.models.generate_content(
            model=os.environ.get("CARDNEWS_MODEL", "gemini-2.5-flash"),
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
    return extract_json(response.text or "")


def normalize_deck(deck, manuscript, title, wanted=10):
    deck = dict(deck or {})
    deck["handle"] = deck.get("handle") or "@aimax"
    deck["preset"] = "cine"
    deck["accent"] = deck.get("accent", None)
    slides = list(deck.get("slides") or [])
    if not slides:
        return fallback_deck(manuscript, title)

    closing = next((s for s in reversed(slides) if s.get("type") == "closing"), None)
    non_closing = [s for s in slides if s is not closing]
    if closing is None:
        closing = fallback_deck(manuscript, title)["slides"][-1]

    while len(non_closing) < wanted - 1:
        n = len([s for s in non_closing if s.get("type") == "content"]) + 1
        points = manuscript_card_points(manuscript, limit=wanted - 1)
        head, desc = points[(n - 1) % len(points)] if points else ("다음 행동을 정한다", "계획을 문서로 끝내지 말고 바로 실행 가능한 작업으로 바꿉니다.")
        non_closing.append({
            "type": "content",
            "f": {"idx": f"{n:02d}", "total": f"{wanted - 2:02d}", "tag": "Point", "num": f"{n:02d}", "head": head, "desc": desc},
        })

    deck["slides"] = non_closing[:wanted - 1] + [closing]
    for i, slide in enumerate(deck["slides"]):
        f = slide.setdefault("f", {})
        for key, max_len in (("title", 42), ("sub", 80), ("head", 34), ("desc", 70), ("quote", 70), ("hint", 50)):
            if key in f:
                f[key] = clean_card_text(f[key], max_len)
                if is_card_slop(f[key]):
                    f[key] = clean_card_text(f[key].replace("거인의 어깨", "검증된 경로").replace("극대화", "높이기").replace("상상 이상", "더").replace("강력한", "실용적인"), max_len)
        if f.get("head") and f.get("desc") and f["head"] == f["desc"]:
            f["desc"] = clean_card_text(manuscript, 58)
        bg_by_style = f.setdefault("bgByStyle", {})
        bg_by_style.setdefault("cine", CARD_BG_POOL[i % len(CARD_BG_POOL)])
    content = [s for s in deck["slides"] if s.get("type") == "content" and s.get("f")]
    total = f"{len(content):02d}"
    for i, s in enumerate(content, 1):
        s["f"]["total"] = total
        if s["f"].get("idx"):
            s["f"]["idx"] = f"{i:02d}"
        if s["f"].get("num"):
            s["f"]["num"] = f"{i:02d}"
    return deck


def make_card_deck(manuscript, title):
    prompt_path = CARDNEWS_ROOT / "tools" / "deck-prompt.md"
    if not prompt_path.exists():
        raise RuntimeError(f"cardnews 프롬프트 파일을 찾지 못했습니다: {prompt_path}")
    prompt = prompt_path.read_text(encoding="utf-8") + CARD_DECK_EXTRA_RULES
    full_prompt = f"{prompt}\n\n==== 변환할 글 ====\n{manuscript}"
    failures = []
    try:
        raw = run_claude(full_prompt, timeout=300)
        deck = normalize_deck(extract_json(raw), manuscript, title, wanted=10)
        issues = deck_validation_issues(deck)
        if not issues:
            return deck
        failures.append("Claude 결과 검증 실패: " + ", ".join(issues))
    except Exception as exc:
        failures.append(f"Claude 생성 실패: {exc}")

    try:
        print("[카드뉴스] Claude 결과를 쓰지 못해 Gemini JSON 생성으로 재시도합니다.")
        deck = normalize_deck(_gemini_card_deck(full_prompt), manuscript, title, wanted=10)
        issues = deck_validation_issues(deck)
        if not issues:
            return deck
        failures.append("Gemini 결과 검증 실패: " + ", ".join(issues))
    except Exception as exc:
        failures.append(f"Gemini 생성 실패: {exc}")

    raise RuntimeError("카드뉴스 10장 품질 검증을 통과하지 못했습니다. " + " | ".join(failures))


def card_deck_claim_text(deck):
    lines = []
    for index, slide in enumerate((deck or {}).get("slides") or [], 1):
        fields = slide.get("f") or {}
        text = " / ".join(
            str(fields.get(key) or "").replace("\n", " ").strip()
            for key in ("title", "sub", "head", "desc", "quote", "by", "rows")
            if str(fields.get(key) or "").strip()
        )
        if text:
            lines.append(f"카드 {index}: {text}")
    return "\n".join(lines)


def ensure_cardnews_server():
    url = f"http://127.0.0.1:{CARDNEWS_PORT}/ping"
    try:
        requests.get(url, timeout=2).raise_for_status()
        return None
    except Exception:
        pass

    server = CARDNEWS_ROOT / "tools" / "server.js"
    if not server.exists():
        raise RuntimeError(f"cardnews 서버 파일을 찾지 못했습니다: {server}")
    env = os.environ.copy()
    env["NO_OPEN"] = "1"
    proc = subprocess.Popen(
        ["node", str(server)],
        cwd=str(CARDNEWS_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for _ in range(30):
        try:
            requests.get(url, timeout=1).raise_for_status()
            return proc
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("cardnews 서버 시작을 확인하지 못했습니다.")


def write_square_card(png_bytes, path):
    from PIL import Image, ImageEnhance, ImageFilter

    src = Image.open(BytesIO(png_bytes)).convert("RGB")
    canvas_w, canvas_h = CARD_SQUARE_SIZE

    bg = src.resize((canvas_w, int(src.height * canvas_w / src.width)))
    top = max(0, (bg.height - canvas_h) // 2)
    bg = bg.crop((0, top, canvas_w, top + canvas_h)).filter(ImageFilter.GaussianBlur(18))
    bg = ImageEnhance.Brightness(bg).enhance(0.42)

    fg_h = canvas_h
    fg_w = int(src.width * fg_h / src.height)
    fg = src.resize((fg_w, fg_h), Image.Resampling.LANCZOS)
    x = (canvas_w - fg_w) // 2
    bg.paste(fg, (x, 0))
    bg.save(path, "PNG", optimize=True)


def render_cardnews_pngs(deck, out_dir, aspect="square"):
    if aspect != "square":
        raise RuntimeError("YouTube 카드뉴스는 1080x1080 정사각형만 허용합니다.")
    if len(deck.get("slides") or []) != 10:
        raise RuntimeError("YouTube 카드뉴스는 정확히 10장이어야 합니다.")
    from playwright.sync_api import sync_playwright

    ensure_cardnews_server()
    out_dir.mkdir(parents=True, exist_ok=True)
    deck_json = json.dumps(deck, ensure_ascii=False)
    paths = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        for i, _slide in enumerate(deck["slides"]):
            viewport = CARD_SQUARE_SIZE
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]}, device_scale_factor=1)
            page.add_init_script(
                f"localStorage.setItem('cardnews-editor-v3', {json.dumps(deck_json)});"
            )
            page.goto(f"http://127.0.0.1:{CARDNEWS_PORT}/?shoot={i}", wait_until="networkidle")
            page.add_style_tag(content=SQUARE_CARD_CSS)
            page.wait_for_timeout(900)
            path = out_dir / f"{i + 1:02d}.png"
            page.screenshot(path=str(path), full_page=False)
            paths.append(str(path))
            page.close()
        browser.close()
    return paths


def capture_youtube_frames_from_browser(youtube_url, image_count, out_dir):
    """Capture YouTube player frames when direct video download is blocked."""
    if image_count <= 0:
        return []
    if aside_available():
        try:
            frame_dir = Path(out_dir) / "youtube_frames"
            return capture_youtube_frames_aside(youtube_url, image_count, frame_dir)
        except Exception as e:
            print(f"[주의] Aside 장면 캡처 실패, 로컬 Playwright fallback 사용: {e}")
    try:
        from PIL import Image, ImageStat
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"[주의] 브라우저 캡처 fallback을 사용할 수 없습니다: {e}")
        return []

    frame_dir = Path(out_dir) / "youtube_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    target_times = [35, 85, 135, 205, 285, 360, 450, 540, 630, 720][:image_count]
    paths = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--mute-audio"],
        )
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        for idx, seconds in enumerate(target_times, 1):
            page.goto(f"{youtube_url.split('&')[0]}&t={seconds}s", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("video", timeout=30000)
            for _ in range(8):
                for selector in (
                    ".ytp-ad-skip-button",
                    ".ytp-skip-ad-button",
                    "button:has-text('건너뛰기')",
                    "button:has-text('Skip')",
                ):
                    try:
                        btn = page.locator(selector).first
                        if btn.count():
                            btn.click(timeout=500)
                            break
                    except Exception:
                        pass
                page.wait_for_timeout(500)
            page.evaluate(
                """async (seconds) => {
                    const video = document.querySelector('video');
                    video.muted = true;
                    video.currentTime = Math.min(seconds, Math.max(0, (video.duration || seconds) - 2));
                    try { await video.play(); } catch (e) {}
                }""",
                seconds,
            )
            page.wait_for_timeout(2500)
            page.mouse.move(1200, 700)
            box = page.locator("video").first.bounding_box()
            path = frame_dir / f"{idx:02d}.png"
            if box:
                page.screenshot(
                    path=str(path),
                    clip={
                        "x": max(0, box["x"]),
                        "y": max(0, box["y"]),
                        "width": min(box["width"], 1280),
                        "height": min(box["height"], 720),
                    },
                )
            else:
                page.screenshot(path=str(path), full_page=False)

            im = Image.open(path).convert("RGB")
            mean = ImageStat.Stat(im).mean
            if max(mean) - min(mean) > 2 or sum(mean) / 3 > 55:
                paths.append(str(path))
            else:
                print(f"[주의] 어두운/빈 캡처 제외: {path.name}")
        browser.close()
    return paths


def build_cafe_assets(manuscript, youtube_url, title, image_count, optional_config, out_dir=None, use_ai_keywords=True):
    if not title:
        title = make_title(manuscript)
    section_count = count_sections(manuscript)
    if image_count is None:
        image_count = min(10, section_count or 10)
    elif section_count:
        image_count = min(int(image_count), section_count)

    image_paths = []
    if youtube_url and image_count > 0:
        cached = []
        if out_dir:
            frame_dir = Path(out_dir) / "youtube_frames"
            cached = sorted(str(path.resolve()) for path in frame_dir.glob("youtube-frame-*.jpg"))
        if len(cached) >= image_count:
            image_paths = cached[:image_count]
            print(f"[카페 이미지] 기존 브라우저 캡처 {len(image_paths)}장을 재사용합니다.")
        else:
            image_paths = auto.extract_frames(youtube_url, image_count)
        if not image_paths and out_dir:
            print("[카페 이미지] 직접 다운로드 캡처 실패. 브라우저 재생 화면 캡처로 재시도합니다.")
            image_paths = capture_youtube_frames_from_browser(youtube_url, image_count, out_dir)
    body = build_body(manuscript, len(image_paths), optional_config, use_ai_keywords=use_ai_keywords)
    return title, body, image_paths


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("youtube_url", nargs="?", help="YouTube URL")
    ap.add_argument("--file", help="NotebookLM 원고 파일을 직접 사용")
    ap.add_argument(
        "--reuse-factcheck-report",
        help="이미 사실확인된 --file 입력과 함께 재사용할 기존 사실확인 JSON",
    )
    ap.add_argument("--title", default="", help="카페/카드뉴스 제목 직접 지정")
    ap.add_argument("--images", type=int, default=10, help="카페용 영상 캡처 장수")
    ap.add_argument("--no-keywords", action="store_true", help="카페 본문 볼드/음영 키워드 선택 끄기")
    ap.add_argument("--cafe-draft", action="store_true", help="네이버 카페에 임시저장까지 진행")
    ap.add_argument("--cafe-publish", action="store_true", help="네이버 카페에 바로 발행")
    ap.add_argument("--youtube-open", action="store_true", help="YouTube 커뮤니티 작성창에 글+이미지까지 채우기")
    ap.add_argument("--youtube-publish", action="store_true", help="YouTube 커뮤니티 글을 바로 게시")
    ap.add_argument("--shorts-publish", action="store_true", help="검증된 쇼츠 MP4를 YouTube에 바로 게시")
    ap.add_argument(
        "--publish-policy",
        choices=["prepare", "immediate", "first-review-then-immediate"],
        default="prepare",
        help="prepare=산출물만, immediate=즉시 발행, first-review-then-immediate=첫 회 텔레그램 승인 후 이후 즉시 발행",
    )
    ap.add_argument("--youtube-community-url", default="", help="채널 게시물 탭 URL 직접 지정")
    ap.add_argument("--youtube-expected-channel", default="나민수 AI", help="게시 전 확인할 YouTube 채널명")
    ap.add_argument("--cardnews-aspect", choices=["square"], default="square", help="카드뉴스 PNG 비율: YouTube 1:1 고정")
    ap.add_argument("--skip-cardnews", action="store_true")
    ap.add_argument("--skip-youtube-text", action="store_true")
    ap.add_argument("--skip-shorts-script", action="store_true", help="전용 NotebookLM 쇼츠 대본 생성을 건너뜀")
    ap.add_argument("--skip-shorts-video", action="store_true", help="쇼츠 MP4 렌더링을 건너뜀(3종 발행 정책과 함께 사용 불가)")
    args = ap.parse_args(argv)

    if not args.youtube_url and not args.file:
        ap.error("youtube_url 또는 --file 중 하나가 필요합니다.")
    if args.reuse_factcheck_report and not args.file:
        ap.error("--reuse-factcheck-report는 --file과 함께 사용해야 합니다.")
    if args.publish_policy != "prepare" and any(
        (args.cafe_draft, args.cafe_publish, args.youtube_open, args.youtube_publish, args.shorts_publish)
    ):
        ap.error("--publish-policy와 기존 개별 작성/발행 옵션은 함께 사용할 수 없습니다.")
    if args.publish_policy != "prepare" and (args.skip_cardnews or args.skip_youtube_text):
        ap.error("자동 발행 정책에는 카페 원고, 카드뉴스, YouTube 본문이 모두 필요합니다.")
    if args.publish_policy != "prepare" and (args.skip_shorts_script or args.skip_shorts_video):
        ap.error("자동 발행 정책에는 쇼츠 대본과 실제 MP4가 모두 필요합니다.")
    if args.publish_policy != "prepare" and not args.youtube_url:
        ap.error("3종 자동 발행 정책에는 원본 YouTube URL이 필요합니다.")

    config, optional_config, nlm_cfg = load_runtime_config()
    video_id = auto.extract_video_id(args.youtube_url or "")
    run_name = slug(args.title or (f"youtube-{video_id}" if video_id else args.youtube_url or args.file))
    out_dir = OUTPUT_ROOT / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.file:
        manuscript = read_text_file(args.file)
    else:
        manuscript = nlm.fetch_manuscript(args.youtube_url, nlm_cfg)
    write_text(out_dir / "01_notebooklm_cafe_manuscript.md", manuscript)
    if args.reuse_factcheck_report:
        factcheck_report = json.loads(read_text_file(args.reuse_factcheck_report))
        if factcheck_report.get("status") not in {"ok", "fixed"}:
            raise RuntimeError("재사용할 사실확인 보고서의 status가 ok/fixed가 아닙니다.")
        print("[사실확인] 기존 교정 원고와 보고서를 재사용합니다.")
    else:
        manuscript, factcheck_report = factcheck_manuscript(manuscript, auto.GEMINI_API_KEY)
    write_text(out_dir / "01b_factchecked_manuscript.md", manuscript)
    (out_dir / "01c_factcheck_report.json").write_text(
        json.dumps(factcheck_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    title, cafe_body, cafe_images = build_cafe_assets(
        manuscript,
        args.youtube_url or "",
        args.title,
        args.images,
        optional_config,
        out_dir=out_dir,
        use_ai_keywords=not args.no_keywords,
    )
    write_text(out_dir / "02_cafe_title.txt", title)
    write_text(out_dir / "03_cafe_body.txt", cafe_body)

    card_paths = []
    card_factcheck = {"status": "not_run"}
    if not args.skip_cardnews:
        deck = make_card_deck(manuscript, title)
        card_claims = card_deck_claim_text(deck)
        checked_card_claims, card_factcheck = factcheck_manuscript(
            card_claims, auto.GEMINI_API_KEY
        )
        if checked_card_claims != card_claims:
            raise RuntimeError(
                "카드뉴스 최종 문구에서 사실 수정이 발생했습니다. "
                "교정 전 카드를 발송하지 않도록 중단합니다."
            )
        deck_path = out_dir / "04_cardnews_deck.json"
        deck_path.write_text(json.dumps(deck, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "04b_cardnews_factcheck_report.json").write_text(
            json.dumps(card_factcheck, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        card_paths = render_cardnews_pngs(deck, out_dir / "cardnews_png", aspect=args.cardnews_aspect)
        (CARDNEWS_ROOT / "editor" / "decks").mkdir(parents=True, exist_ok=True)
        (CARDNEWS_ROOT / "editor" / "decks" / f"{run_name}.json").write_text(
            json.dumps(deck, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    youtube_post = ""
    youtube_factcheck = {"status": "not_run"}
    if not args.skip_youtube_text:
        youtube_post = make_youtube_post(manuscript)
        youtube_post, youtube_factcheck = factcheck_manuscript(
            youtube_post, auto.GEMINI_API_KEY
        )
        if not youtube_post.rstrip().endswith(YOUTUBE_FINAL_LINE):
            raise RuntimeError("사실확인 과정에서 YouTube 고정 CTA가 변경되어 중단했습니다.")
        write_text(out_dir / "05_youtube_community_post.txt", youtube_post)
        (out_dir / "05b_youtube_factcheck_report.json").write_text(
            json.dumps(youtube_factcheck, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    shorts_script = ""
    shorts_factcheck = {"status": "not_run"}
    shorts_video_result = {}
    shorts_video_path = ""
    if args.youtube_url and not args.skip_shorts_script:
        (
            shorts_script,
            shorts_format,
            shorts_minutes,
            shorts_raw,
            shorts_factcheck,
            shorts_head_copies,
        ) = shorts.fetch(args.youtube_url)
        shorts_headline = shorts_head_copies[0]
        write_text(out_dir / "06_notebooklm_shorts_raw.txt", shorts_raw)
        write_text(out_dir / "07_shorts_script_through_fifth.txt", shorts_script)
        write_text(out_dir / "08_shorts_headline.txt", shorts_headline)
        (out_dir / "08_shorts_metadata.json").write_text(
            json.dumps(
                {
                    "notebook_format": shorts_format,
                    "source_minutes": shorts_minutes,
                    "headline": shorts_headline,
                    "headline_candidates": shorts_head_copies,
                    "factcheck": shorts_factcheck,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if not args.skip_shorts_video:
            shorts_title = normalize_shorts_title(title)
            shorts_video_result = render_short_video(
                shorts_script,
                cafe_images,
                out_dir,
                title=shorts_title,
                headline=shorts_headline,
                headline_candidates=shorts_head_copies,
                description=(
                    f"{title}의 핵심 내용을 쇼츠로 정리했습니다.\n\n"
                    "#AI자동화 #Shorts"
                ),
            )
            shorts_video_path = shorts_video_result["path"]

    cafe_publish = args.cafe_publish
    youtube_publish = args.youtube_publish
    shorts_publish = args.shorts_publish
    first_state = None
    first_review_mode = False
    approval_status = "not_required"
    if args.publish_policy == "immediate":
        cafe_publish = youtube_publish = shorts_publish = True
    elif args.publish_policy == "first-review-then-immediate":
        first_state = load_first_publish_state()
        if first_state.get("first_review_completed"):
            cafe_publish = youtube_publish = shorts_publish = True
        else:
            first_review_mode = True
            fingerprint = publish_fingerprint(
                args.youtube_url or "", title, cafe_body, youtube_post, shorts_script
            )
            if first_state.get("approved_fingerprint") != fingerprint:
                if not cafe_images:
                    raise RuntimeError("카페 이미지가 0장이라 승인 요청을 중단합니다.")
                if not card_paths:
                    raise RuntimeError("카드뉴스 이미지가 0장이라 승인 요청을 중단합니다.")
                print("[승인] 첫 발행 산출물을 텔레그램으로 보내고 승인을 기다립니다.")
                approved = request_publish_approval(
                    config,
                    source_url=args.youtube_url or "직접 원고",
                    cafe_title=title,
                    cafe_body=cafe_body,
                    youtube_body=youtube_post,
                    card_images=card_paths[:10],
                    factcheck_status=(
                        "fixed"
                        if "fixed" in {
                            factcheck_report.get("status"),
                            card_factcheck.get("status"),
                            youtube_factcheck.get("status"),
                            shorts_factcheck.get("status"),
                        }
                        else factcheck_report.get("status", "")
                    ),
                    shorts_body=shorts_script,
                )
                if not approved:
                    approval_status = "rejected"
                    summary = {
                        "status": "held",
                        "output_dir": str(out_dir),
                        "title": title,
                        "approval": approval_status,
                        "cafe_post_mode": "not_posted",
                        "youtube_mode": "not_posted",
                        "shorts_mode": "not_posted",
                    }
                    print(json.dumps(summary, ensure_ascii=False, indent=2))
                    return 2
                first_state = {
                    "approved_fingerprint": fingerprint,
                    "source_id": auto._publish_source_key(args.youtube_url or title),
                    "cafe_published": False,
                    "youtube_published": False,
                    "shorts_published": False,
                    "first_review_completed": False,
                }
                save_first_publish_state(first_state)
            approval_status = "approved"
            cafe_publish = not first_state.get("cafe_published", False)
            youtube_publish = not first_state.get("youtube_published", False)
            shorts_publish = not first_state.get("shorts_published", False)

    bundle_fingerprint = publish_fingerprint(
        args.youtube_url or "", title, cafe_body, youtube_post, shorts_script
    )
    bundle_status = load_bundle_publish_status(out_dir)
    if bundle_status.get("fingerprint") != bundle_fingerprint:
        bundle_status = {
            "fingerprint": bundle_fingerprint,
            "source_id": auto._publish_source_key(args.youtube_url or title),
            "cafe_published": False,
            "youtube_published": False,
            "shorts_published": False,
            "shorts_url": "",
            "status": "prepared",
        }
    cafe_publish = bool(cafe_publish and not bundle_status.get("cafe_published"))
    youtube_publish = bool(youtube_publish and not bundle_status.get("youtube_published"))
    shorts_publish = bool(shorts_publish and not bundle_status.get("shorts_published"))
    save_bundle_publish_status(out_dir, bundle_status)

    if args.cafe_draft or cafe_publish:
        if not cafe_images:
            raise RuntimeError("카페 이미지가 0장이라 작성/발행을 중단합니다.")
        cafe_result = auto.post_to_naver_cafe(
            title,
            cafe_body,
            cafe_images,
            optional_config,
            source_url=args.youtube_url,
            draft=not cafe_publish,
        )
        if cafe_publish:
            if cafe_result.get("status") != "published":
                raise RuntimeError(f"네이버 카페 발행 완료 상태를 확인하지 못했습니다: {cafe_result}")
            track_publication("naver_cafe", auto._publish_source_key(args.youtube_url or title))
            bundle_status["cafe_published"] = True
            bundle_status["cafe_source_id"] = bundle_status["source_id"]
            bundle_status["cafe_url"] = cafe_result.get("url", "")
            bundle_status["status"] = "partial"
            save_bundle_publish_status(out_dir, bundle_status)
            if first_review_mode:
                first_state["cafe_published"] = True
                first_state["cafe_source_id"] = bundle_status["source_id"]
                first_state["cafe_url"] = cafe_result.get("url", "")
                save_first_publish_state(first_state)

    youtube_mode = (
        "already_published"
        if first_review_mode and first_state.get("youtube_published", False)
        else "not_opened"
    )
    if args.youtube_open or youtube_publish:
        if not youtube_post:
            youtube_post = make_youtube_post(manuscript)
            write_text(out_dir / "05_youtube_community_post.txt", youtube_post)
        if not card_paths:
            raise RuntimeError("YouTube 이미지 업로드를 하려면 카드뉴스 PNG가 필요합니다. --skip-cardnews를 빼고 실행하세요.")
        yt_result = post_to_youtube_community(
            youtube_post,
            card_paths[:10],
            publish=youtube_publish,
            community_url=args.youtube_community_url,
            expected_channel=args.youtube_expected_channel,
        )
        youtube_mode = yt_result.get("status", "opened")
        expected_yt_status = "published" if youtube_publish else "filled"
        if youtube_mode != expected_yt_status:
            raise RuntimeError(f"YouTube 게시 완료 상태를 확인하지 못했습니다: {yt_result}")
        if youtube_publish:
            track_publication("youtube_community", auto._publish_source_key(args.youtube_url or title))
            bundle_status["youtube_published"] = True
            bundle_status["youtube_source_id"] = bundle_status["source_id"]
            bundle_status["youtube_url"] = yt_result.get("url", "")
            bundle_status["status"] = "partial"
            save_bundle_publish_status(out_dir, bundle_status)
            if first_review_mode:
                first_state["youtube_published"] = True
                first_state["youtube_source_id"] = bundle_status["source_id"]
                first_state["youtube_url"] = yt_result.get("url", "")
                save_first_publish_state(first_state)

    shorts_mode = "already_published" if bundle_status.get("shorts_published") else "not_uploaded"
    shorts_result = {}
    if shorts_publish:
        if not shorts_video_path:
            candidate = out_dir / "09_shorts_final.mp4"
            if candidate.is_file():
                shorts_video_path = str(candidate)
        if not shorts_video_path:
            raise RuntimeError("쇼츠 MP4가 없어 3종 발행을 중단합니다.")
        validation = validate_short_video(shorts_video_path)
        voice_validation = validate_minsoo_voice_artifact(shorts_video_path)
        from shorts_video import validate_upload_ready
        policy_validation = validate_upload_ready(shorts_video_path)
        metadata_path = out_dir / "10_shorts_upload.json"
        upload_metadata = (
            json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata_path.is_file()
            else {}
        )
        shorts_result = upload_youtube_short(
            shorts_video_path,
            upload_metadata.get("title") or (title[:88].rstrip() + " #Shorts")[:100],
            upload_metadata.get("description") or "",
            publish=True,
            expected_channel=args.youtube_expected_channel,
        )
        if shorts_result.get("status") != "published" or not shorts_result.get("video_id"):
            raise RuntimeError(f"YouTube Shorts 게시 완료 상태를 확인하지 못했습니다: {shorts_result}")
        shorts_mode = "published"
        bundle_status["shorts_voice_validation"] = voice_validation
        bundle_status["shorts_policy_validation"] = policy_validation
        track_publication("youtube_shorts", auto._publish_source_key(args.youtube_url or title))
        bundle_status["shorts_published"] = True
        bundle_status["shorts_source_id"] = bundle_status["source_id"]
        bundle_status["shorts_url"] = shorts_result.get("url", "")
        bundle_status["shorts_video_id"] = shorts_result.get("video_id", "")
        bundle_status["shorts_validation"] = validation
        bundle_status["status"] = "partial"
        save_bundle_publish_status(out_dir, bundle_status)
        if first_review_mode:
            first_state["shorts_published"] = True
            first_state["shorts_source_id"] = bundle_status["source_id"]
            first_state["shorts_url"] = shorts_result.get("url", "")
            save_first_publish_state(first_state)

    if first_review_mode and three_channel_complete(first_state):
        first_state["first_review_completed"] = True
        save_first_publish_state(first_state)

    all_published = three_channel_complete(bundle_status)
    if all_published:
        bundle_status["status"] = "published"
        save_bundle_publish_status(out_dir, bundle_status)

    summary = {
        "status": "published" if all_published else "prepared",
        "output_dir": str(out_dir),
        "title": title,
        "cardnews_images": card_paths,
        "youtube_post_file": str(out_dir / "05_youtube_community_post.txt") if youtube_post else "",
        "shorts_script_file": str(out_dir / "07_shorts_script_through_fifth.txt") if shorts_script else "",
        "shorts_video_file": shorts_video_path,
        "shorts_validation": shorts_video_result or bundle_status.get("shorts_validation", {}),
        "shorts_mode": shorts_mode,
        "shorts_url": bundle_status.get("shorts_url", ""),
        "publish_status_file": str(out_dir / "publish_status.json"),
        "approval": approval_status,
        "factcheck": factcheck_report.get("status", ""),
        "cafe_post_mode": "publish" if cafe_publish else "draft" if args.cafe_draft else "already_published" if first_review_mode and first_state.get("cafe_published") else "not_posted",
        "youtube_mode": youtube_mode,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.publish_policy != "prepare" and not all_published:
        return 3
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit(1)
