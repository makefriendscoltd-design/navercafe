"""Generate Shorts narration without rewriting the NotebookLM script body.

Only items through the fifth are retained. Any sixth-and-later material and
the notebook's own CTA are replaced by the owner's duration-aware CTA. No
fact-checking or editorial pass is allowed to rewrite the narration body.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import math
import re
from pathlib import Path

import notebooklm_source as nlm
from content_production_policy import ASIDE_ACCOUNT, SHORTS_NOTEBOOK, validate_notebook_binding


SCRIPT_DIR = Path(__file__).resolve().parent
MIGRATED_CONFIG = Path.home() / "orca" / "projects" / "ccidacafe" / "config.ini"
SHORTS_NOTEBOOK_TITLE = SHORTS_NOTEBOOK["title"]
SHORTS_NOTEBOOK_ID = SHORTS_NOTEBOOK["id"]
SHORTS_PROMPT = "이 영상으로 숏폼 스크립트 만들어줘."

SCRIPT_HEADER_RE = re.compile(r"^(?:#{1,4}\s*)?스크립트\s*$", re.M)
HEAD_COPY_HEADER_RE = re.compile(r"^(?:#{1,4}\s*)?헤드카피(?:라이팅)?\s*$", re.M)
FORMAT_RE = re.compile(r"^(?:#{1,4}\s*)?선택한 포맷\s*$\s*(.+)$", re.M)
FIFTH_RE = re.compile(r"(?m)^\s*(?:[-*>#]+\s*)?(?:5\s*(?:번|번째)?\s*[.)、:]|⑤|다섯째\s*[,.:]?)")
SIXTH_RE = re.compile(r"(?m)^\s*(?:[-*>#]+\s*)?(?:6\s*(?:번|번째)?\s*[.)、:]|⑥|여섯째\s*[,.:]?)")
OLD_CTA_RE = re.compile(
    r"(?mi)^.*(?:이 영상을 정리했습니다|자료가 궁금하신 분|채널(?:을)?\s*구독|"
    r"프로필 링크|댓글에\s*\S+\s*남겨|무료\s*(?:가이드|자료|정보)).*$"
)
STRONG_HOOK_RE = re.compile(
    r"^(?:여기,\s*)?이\s*(?:남자들?|사람들?|프로그램|도구|기능).*(?:"
    r"미쳤습니다|대박입니다|천재입니다|신입니다|고수입니다|벌었습니다|만들었습니다)\.?$"
)
HEAD_COPY_ITEM_RE = re.compile(r"^\s*(?:[1-3]\s*[.)、:]|[①②③])\s*(.+?)\s*$")
HEAD_COPY_SPOKEN_RE = re.compile(
    r"(?:[?!]|(?:습니다|니다|있다|된다|바뀐다|끝이다|가능하다|임|함|잖아|네|죠)[.!]?$|"
    r"(?:손해|충분|끝|가능)[.!]?$)"
)
HEAD_COPY_STOP_WORDS = {
    "이거", "그냥", "진짜", "오늘", "지금", "방법", "하는법", "전략", "충분",
}


def head_copy_lines(value: str) -> tuple[str, str]:
    """Normalize one two-line head copy candidate."""
    text = (value or "").strip().replace("／", "/")
    if "/" in text:
        parts = [part.strip() for part in text.split("/")]
    else:
        parts = [part.strip() for part in text.splitlines() if part.strip()]
    if len(parts) != 2 or not all(parts):
        raise RuntimeError("쇼츠 헤드카피는 정확히 2줄이어야 합니다.")
    return parts[0], parts[1]


def validate_head_copy(value: str) -> str:
    """Require the owner's spoken two-line first-screen copy shape."""
    first, second = head_copy_lines(value)
    if any(len(line) > 18 for line in (first, second)):
        raise RuntimeError("쇼츠 헤드카피는 한 줄당 18자 이하여야 합니다.")
    if any(len(re.sub(r"\s+", "", line)) < 4 for line in (first, second)):
        raise RuntimeError("쇼츠 헤드카피가 너무 짧습니다.")
    if "#" in first or "#" in second:
        raise RuntimeError("쇼츠 헤드카피에는 해시태그를 넣지 않습니다.")
    if not HEAD_COPY_SPOKEN_RE.search(first):
        raise RuntimeError(
            "쇼츠 헤드카피 첫 줄은 질문·놀람·손해감·강한 단정의 구어체여야 합니다."
        )
    return f"{first}\n{second}"


def validate_head_copy_connection(value: str, script: str) -> str:
    """Require the headline promise to share a concrete idea with the opening."""
    normalized = validate_head_copy(value)
    opening_lines = [
        line.strip() for line in (script or "").splitlines() if line.strip()
    ]
    opening = " ".join(opening_lines[:3]).lower()
    tokens = re.findall(r"[가-힣A-Za-z0-9]+", normalized)
    keywords = []
    for token in tokens:
        lowered = token.lower()
        lowered = re.sub(r"(?:으로|에서|에게|부터|까지|처럼|보다|은|는|이|가|을|를|도|로|의|와|과)$", "", lowered)
        if len(lowered) >= 2 and lowered not in HEAD_COPY_STOP_WORDS:
            keywords.append(lowered)
    if not keywords or not any(keyword in opening for keyword in keywords):
        raise RuntimeError("쇼츠 헤드카피와 스크립트 초반 내용이 연결되지 않습니다.")
    return normalized


def extract_head_copy_candidates(answer: str) -> list[str]:
    """Extract and validate three ranked two-line candidates from NotebookLM."""
    match = HEAD_COPY_HEADER_RE.search(answer or "")
    if not match:
        raise RuntimeError("노트북 응답에 '### 헤드카피라이팅' 절이 없습니다.")
    tail = (answer[match.end():] or "").strip()
    script_heading = SCRIPT_HEADER_RE.search(tail)
    block = tail[:script_heading.start()].strip() if script_heading else tail
    candidates = []
    for line in block.splitlines():
        if not line.strip():
            continue
        item = HEAD_COPY_ITEM_RE.match(line)
        if item:
            candidates.append(validate_head_copy(item.group(1)))
        elif "/" in line or "／" in line:
            candidates.append(validate_head_copy(line))
    if len(candidates) != 3:
        raise RuntimeError("쇼츠 헤드카피 후보가 정확히 3개가 아닙니다.")
    if len(set(candidates)) != 3:
        raise RuntimeError("쇼츠 헤드카피 후보 3개가 서로 달라야 합니다.")
    return candidates


def extract_script(answer: str) -> tuple[str, str]:
    chosen = ""
    match = FORMAT_RE.search(answer or "")
    if match:
        chosen = match.group(1).strip()
    match = SCRIPT_HEADER_RE.search(answer or "")
    if not match:
        raise RuntimeError("노트북 응답에 '### 스크립트' 절이 없습니다.")
    body = (answer[match.end():] or "").strip()
    lines = [line.rstrip() for line in body.splitlines()]
    body = "\n".join(line for line in lines if line.strip() not in (".", "·", "-")).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    if not body:
        raise RuntimeError("노트북의 스크립트 절이 비어 있습니다.")
    return body, chosen


def keep_through_fifth(script: str) -> str:
    """Cut at item six and remove any prior CTA before appending ours."""
    text = (script or "").strip()
    fifth = FIFTH_RE.search(text)
    sixth = SIXTH_RE.search(text)
    if sixth:
        if not fifth or fifth.start() > sixth.start():
            raise RuntimeError("쇼츠 스크립트의 5번째 항목을 확인할 수 없습니다.")
        text = text[:sixth.start()].rstrip()

    cta = OLD_CTA_RE.search(text)
    if cta and (not fifth or cta.start() > fifth.start()):
        text = text[:cta.start()].rstrip()
    if not text:
        raise RuntimeError("5번째 항목까지 남긴 쇼츠 스크립트가 비어 있습니다.")
    return text


def duration_minutes(seconds: float) -> int:
    if not seconds or seconds <= 0:
        raise RuntimeError("원본 영상 길이를 확인하지 못했습니다.")
    return max(1, int(math.floor(float(seconds) / 60 + 0.5)))


def fixed_cta(minutes: int) -> str:
    return (
        f"{minutes}분 짜리 영상 내용을 모두 정리했습니다.\n\n"
        "이 자료 궁금하신 분들은 채널을 구독후 프로필 링크를 확인하세요."
    )


def require_strong_hook(script: str) -> str:
    first_line = next((line.strip() for line in (script or "").splitlines() if line.strip()), "")
    first_sentence = re.split(r"(?<=[.!?])\s+", first_line, maxsplit=1)[0]
    if not STRONG_HOOK_RE.match(first_sentence):
        raise RuntimeError(
            "쇼츠 첫 문장이 기존 강한 후킹 구조(이 남자/이 프로그램)와 다릅니다."
        )
    return first_sentence


def finalize_script(script: str, minutes: int) -> str:
    return f"{keep_through_fifth(script)}\n\n{fixed_cta(minutes)}"


def cta_only_transform_report(script: str, final: str, minutes: int) -> dict:
    """Prove that the NotebookLM body was preserved and only its CTA changed."""
    expected_body = keep_through_fifth(script)
    expected_final = f"{expected_body}\n\n{fixed_cta(minutes)}"
    if final != expected_final:
        raise RuntimeError("NotebookLM 원고 본문이 CTA 교체 외에 변경되었습니다.")
    body_hash = hashlib.sha256(expected_body.encode("utf-8")).hexdigest()
    return {
        "status": "cta_only",
        "notebooklm_body_preserved_exactly": True,
        "content_rewrite_applied": False,
        "removed_notebooklm_cta": True,
        "fixed_cta_applied": True,
        "body_sha256_before": body_hash,
        "body_sha256_after": body_hash,
    }


def get_video_duration(url: str) -> float:
    import yt_dlp

    options = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    return float((info or {}).get("duration") or 0)


def load_shorts_config():
    cfg = configparser.RawConfigParser()
    local = SCRIPT_DIR / "config.ini"
    config_path = local if local.exists() else MIGRATED_CONFIG
    if config_path.exists():
        cfg.read(config_path, encoding="utf-8")
    result = nlm.load_config(cfg)
    validate_notebook_binding(
        "shorts",
        account=ASIDE_ACCOUNT,
        title=SHORTS_NOTEBOOK_TITLE,
        notebook_id=SHORTS_NOTEBOOK_ID,
    )
    # Keep the exact existing notebook identity in generated evidence.  The
    # provider interaction itself must be performed through Aside u0.
    result["notebook_id"] = SHORTS_NOTEBOOK_ID
    result["notebook_title"] = SHORTS_NOTEBOOK_TITLE
    result["aside_account"] = ASIDE_ACCOUNT
    result["prompt"] = SHORTS_PROMPT
    result["retry_if_no_heading"] = False
    result["strip_promo"] = False
    # Keep the existing two-value API for callers, but Shorts no longer uses
    # an API key because the NotebookLM body must not pass through a rewrite.
    return result, ""


def fetch(
    url: str,
    log=print,
    *,
    evidence_dir: str | Path | None = None,
) -> tuple[str, str, int, str, dict, list[str]]:
    cfg, _unused_api_key = load_shorts_config()
    if evidence_dir:
        cfg["evidence_dir"] = str(Path(evidence_dir).expanduser().resolve())
    answer = nlm.fetch_manuscript(url, cfg, log=log)
    head_copies = extract_head_copy_candidates(answer)
    script, chosen = extract_script(answer)
    minutes = duration_minutes(get_video_duration(url))
    final = finalize_script(script, minutes)
    transform = cta_only_transform_report(script, final, minutes)
    require_strong_hook(final)
    validate_head_copy_connection(head_copies[0], final)
    return final, chosen, minutes, answer, transform, head_copies


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NotebookLM 쇼츠 대본 5번까지 + 고정 CTA")
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--raw-out")
    parser.add_argument("--headline-out")
    parser.add_argument("--evidence-dir")
    args = parser.parse_args(argv)

    script, chosen, minutes, raw, transform, head_copies = fetch(
        args.url,
        evidence_dir=args.evidence_dir,
    )
    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(script, encoding="utf-8")
    if args.raw_out:
        raw_path = Path(args.raw_out).expanduser().resolve()
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(raw, encoding="utf-8")
    if args.headline_out:
        headline_path = Path(args.headline_out).expanduser().resolve()
        headline_path.parent.mkdir(parents=True, exist_ok=True)
        headline_path.write_text(head_copies[0] + "\n", encoding="utf-8")
    if args.evidence_dir:
        evidence_path = Path(args.evidence_dir).expanduser().resolve()
        evidence_path.mkdir(parents=True, exist_ok=True)
        (evidence_path / "cta-transform.json").write_text(
            json.dumps(transform, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"노트북 포맷: {chosen or '표기 없음'}")
    print("최종 헤드카피: " + " / ".join(head_copy_lines(head_copies[0])))
    print(f"원고 변환: {transform.get('status', '확인 필요')}")
    print(f"원본 영상 길이: {minutes}분 / 5번째 항목까지 사용 / 고정 CTA 적용")
    print(f"쇼츠 나레이션 저장: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
