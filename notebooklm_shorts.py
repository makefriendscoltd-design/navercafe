"""Generate Shorts narration without rewriting the NotebookLM script body.

Only items through the fifth are retained. Any sixth-and-later material and
the notebook's own CTA are replaced by the owner's duration-aware CTA. No
fact-checking or editorial pass is allowed to rewrite the narration body.
"""

from __future__ import annotations

import argparse
import configparser
import fcntl
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import notebooklm_source as nlm
from content_production_policy import (
    ASIDE_ACCOUNT,
    SHORTS_NOTEBOOK,
    SHORTS_NOTEBOOK_INSTRUCTION_SHA256,
    SHORTS_NOTEBOOK_INSTRUCTION_VERSION,
    SHORTS_NOTEBOOK_PROMPT,
    validate_headline_pixel_width,
    validate_notebook_binding,
    validate_recovered_provider_answer,
    validate_shorts_notebook_retry,
    validate_shorts_verbatim_claims,
)


SCRIPT_DIR = Path(__file__).resolve().parent
MIGRATED_CONFIG = Path.home() / "orca" / "projects" / "ccidacafe" / "config.ini"
SHORTS_NOTEBOOK_TITLE = SHORTS_NOTEBOOK["title"]
SHORTS_NOTEBOOK_ID = SHORTS_NOTEBOOK["id"]
SHORTS_PROMPT = SHORTS_NOTEBOOK_PROMPT

SCRIPT_HEADER_RE = re.compile(r"^(?:#{1,4}\s*)?스크립트\s*$", re.M)
HEAD_COPY_HEADER_RE = re.compile(r"^(?:#{1,4}\s*)?헤드카피(?:라이팅)?\s*$", re.M)
FORMAT_RE = re.compile(r"^(?:#{1,4}\s*)?선택한 포맷\s*$\s*(.+)$", re.M)
FIFTH_RE = re.compile(r"(?m)^\s*(?:[-*>#]+\s*)?(?:5\s*(?:번|번째)?\s*[.)、:]|⑤|다섯째\s*[,.:]?)")
SIXTH_RE = re.compile(r"(?mi)^\s*(?:[-*>#]+\s*)?(?:6\s*(?:번|번째)?\s*[.)、:]|6th\s*[,.:)]|⑥|여섯째\s*[,.:]?)")
SEVENTH_OR_LATER_RE = re.compile(
    r"(?mi)^\s*(?:[-*>#]+\s*)?(?:[7-9]\d*\s*(?:번|번째)?\s*[.)、:]|(?:7th|8th|9th|\d+(?:st|nd|rd|th))\s*[,.:)]|[⑦-⑳]|"
    r"(?:일곱|여덟|아홉|열|십)째\s*[,.:]?)"
)
ORDINAL_LINE_PATTERNS = {
    "first": re.compile(r"(?m)^\s*(?:[-*>#]+\s*)?(?:1\s*(?:번|번째)?\s*[.)、:]|①|첫째\s*[,.:]?)"),
    "second": re.compile(r"(?m)^\s*(?:[-*>#]+\s*)?(?:2\s*(?:번|번째)?\s*[.)、:]|②|둘째\s*[,.:]?)"),
    "third": re.compile(r"(?m)^\s*(?:[-*>#]+\s*)?(?:3\s*(?:번|번째)?\s*[.)、:]|③|셋째\s*[,.:]?)"),
    "fourth": re.compile(r"(?m)^\s*(?:[-*>#]+\s*)?(?:4\s*(?:번|번째)?\s*[.)、:]|④|넷째\s*[,.:]?)"),
    "fifth": re.compile(r"(?m)^\s*(?:[-*>#]+\s*)?(?:5\s*(?:번|번째)?\s*[.)、:]|⑤|다섯째\s*[,.:]?)"),
}
NOTEBOOKLM_SCRIPT_PARAGRAPH_LABELS = ("첫째,", "둘째,", "셋째,", "넷째,", "다섯째,")
POST_FIFTH_METADATA_RE = re.compile(
    r"(?i)(?:버전|지침\s*요약|메타데이터|메타\s*주석|주석)\s*:|"
    r"\[(?:버전|지침\s*요약|메타데이터|주석)\]|"
    r"(?:[.!?]\s+)(?:버전\s+v?\d|지침\s*요약\s+(?:완료|준수)|"
    r"메타데이터\s+(?:없음|완료)|주석\s+(?:없음|완료))"
)
SCRIPT_META_RE = re.compile(
    r"(?mi)^\s*(?:#{1,6}\s+|\[(?:훅|본문|결론|CTA|메타[^\]]*)\]|"
    r"(?:훅|본문|결론|CTA|메타\s*주석)\s*:|\[?\d{1,2}:\d{2}(?::\d{2})?\]?)"
)
OLD_CTA_RE = re.compile(
    r"(?mi)^.*(?:이 영상을 정리했습니다|자료가 궁금하신 분|채널(?:을)?\s*구독|"
    r"프로필 링크|댓글에\s*\S+\s*남겨|무료\s*(?:가이드|자료|정보)).*$"
)
STRONG_HOOK_RE = re.compile(
    r"^(?:여기,\s*)?이\s*(?:남자들?|여자들?|사람들?|프로그램|도구|기능).*(?:"
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

DEFAULT_COMMENT_KEYWORD = "자료"
COMMENT_KEYWORD_RE = re.compile(r"^[가-힣]{2}$")
COMMENT_KEYWORD_PARTICLE_RE = re.compile(
    r"(?:으로|에서|에게|부터|까지|처럼|보다|이랑|하고|은|는|이|가|을|를|도|로|의|와|과|만|요)$"
)
# 2글자여도 명사가 아니라 조사·어미·부사·대명사·CTA 상투어인 것들.
COMMENT_KEYWORD_STOP_WORDS = HEAD_COPY_STOP_WORDS | {
    "이것", "저것", "그것", "이런", "저런", "그런", "어떤", "무슨", "여기", "거기", "저기",
    "우리", "여러", "모두", "모든", "전부", "정말", "바로", "아주", "매우", "너무", "완전",
    "이제", "이미", "아직", "항상", "계속", "결국", "물론", "먼저", "이후", "이전", "다음",
    "다시", "만약", "그래", "그럼", "근데", "그냥", "역시", "제일", "가장", "거의", "조금",
    "하나", "둘째", "셋째", "넷째", "다섯", "여섯", "일곱", "첫째", "마지막", "번째",
    "때문", "경우", "정도", "이유", "통해", "위해", "대해", "관해", "사실", "대신",
    "남자", "여자", "사람", "분들", "여러분", "대표", "민수", "구독", "채널", "댓글",
    "영상", "내용", "정리", "자료", "링크", "무료", "가이드", "프로", "미쳤", "대박",
    "천재", "고수", "한번", "한다", "된다", "있다", "없다", "합니", "입니", "됩니",
    "습니", "니다", "하면", "되면", "해서", "하고", "하는", "되는", "이고", "이며",
    "오늘", "내일", "어제", "올해", "작년", "시간", "분량", "부분", "전체", "기본",
    "가능", "필요", "사용", "이용", "확인", "생각", "느낌", "얘기", "이야기", "말씀",
    # 어미 없이 2글자로 남는 동사 어간.
    "만들", "바꾸", "보여", "넣어", "쓰는", "쓰면", "해봐", "해요", "하자", "되게",
}
COMMENT_KEYWORD_VERB_ENDINGS = tuple("면는게서고다까죠요니데나든야네며지어아워줘봐꿔려")

ATTEMPT_LEDGER_SCHEMA = "shorts-notebook-attempt-ledger/v1"


def _text_sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _video_id(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", url or "")
    if not match:
        raise RuntimeError("쇼츠 원본 source_key를 YouTube URL에서 확인하지 못했습니다.")
    return match.group(1)


def _source_output_root(evidence_dir: str | Path | None, source_key: str) -> Path | None:
    if not evidence_dir:
        return None
    current = Path(evidence_dir).expanduser().resolve()
    for candidate in (current, *current.parents):
        if candidate.parent.name == "outputs" and candidate.name.startswith(source_key + "-"):
            return candidate
    return None


def _default_attempt_ledger_path(
    source_key: str, evidence_dir: str | Path | None
) -> tuple[Path, Path | None]:
    source_root = _source_output_root(evidence_dir, source_key)
    if source_root is not None:
        return source_root / "shorts" / "notebooklm-attempt-ledger.json", source_root
    return SCRIPT_DIR / "outputs" / ".notebooklm-shorts-attempts" / f"{source_key}.json", None


def _read_json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"쇼츠 NotebookLM 시도 증거를 읽지 못했습니다: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"쇼츠 NotebookLM 시도 증거가 JSON 객체가 아닙니다: {path}")
    return value


def load_shorts_attempt_evidence(
    source_key: str, ledger_path: Path, source_root: Path | None = None
) -> list[dict]:
    """Load the stable ledger plus provider/attempt evidence already on disk."""
    records: list[dict] = []
    if ledger_path.is_file():
        ledger = _read_json_object(ledger_path)
        if ledger.get("schemaVersion") != ATTEMPT_LEDGER_SCHEMA or ledger.get("sourceKey") != source_key:
            raise RuntimeError("쇼츠 NotebookLM 시도 ledger의 schema/source_key가 다릅니다.")
        values = ledger.get("attempts")
        if not isinstance(values, list):
            raise RuntimeError("쇼츠 NotebookLM 시도 ledger 목록이 없습니다.")
        records.extend(value for value in values if isinstance(value, dict))

    if source_root is not None and source_root.is_dir():
        for path in sorted((source_root / "shorts").glob("**/*attempt_report.json")):
            payload = _read_json_object(path)
            if str(payload.get("sourceKey") or payload.get("source_key") or "") != source_key:
                continue
            instruction = payload.get("instruction") or {}
            if payload.get("status") == "failed" and instruction.get("version") and instruction.get("sha256"):
                records.append({
                    "source_key": source_key,
                    "instruction_version": instruction["version"],
                    "instruction_sha256": instruction["sha256"],
                    "attempt_status": "substantive_failed",
                    "substantive_failure": True,
                    "evidence_path": str(path),
                })
        for path in sorted((source_root / "shorts").glob("**/notebooklm-provider-evidence.json")):
            payload = _read_json_object(path)
            instruction = payload.get("instructionEvidence") or {}
            source_url = str(payload.get("sourceUrl") or "")
            if not source_url:
                continue
            try:
                evidence_source_key = _video_id(source_url)
            except RuntimeError:
                raise RuntimeError(
                    f"NotebookLM provider 시도 증거의 source URL이 정확하지 않습니다: {path}"
                )
            if evidence_source_key != source_key:
                continue
            if payload.get("status") == "ok" and instruction.get("version") and instruction.get("sha256"):
                records.append({
                    "source_key": source_key,
                    "instruction_version": instruction["version"],
                    "instruction_sha256": instruction["sha256"],
                    "attempt_status": "provider_response_received",
                    "evidence_path": str(path),
                })
    return records


def _write_attempt_ledger(ledger_path: Path, source_key: str, attempts: list[dict]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": ATTEMPT_LEDGER_SCHEMA,
        "sourceKey": source_key,
        "attempts": attempts,
    }
    temporary = ledger_path.with_name(f".{ledger_path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(ledger_path)


def record_shorts_attempt(
    ledger_path: Path,
    source_key: str,
    *,
    attempt_id: str,
    status: str,
    substantive_failure: bool = False,
) -> None:
    """Atomically append or update one source-scoped instruction-pinned attempt."""
    lock_path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        attempts = []
        if ledger_path.is_file():
            payload = _read_json_object(ledger_path)
            if payload.get("schemaVersion") != ATTEMPT_LEDGER_SCHEMA or payload.get("sourceKey") != source_key:
                raise RuntimeError("쇼츠 NotebookLM 시도 ledger의 schema/source_key가 다릅니다.")
            attempts = list(payload.get("attempts") or [])
        record = next((item for item in attempts if item.get("attempt_id") == attempt_id), None)
        if record is None:
            record = {
                "attempt_id": attempt_id,
                "source_key": source_key,
                "instruction_version": SHORTS_NOTEBOOK_INSTRUCTION_VERSION,
                "instruction_sha256": SHORTS_NOTEBOOK_INSTRUCTION_SHA256,
            }
            attempts.append(record)
        record.update({
            "attempt_status": status,
            "substantive_failure": bool(substantive_failure),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        _write_attempt_ledger(ledger_path, source_key, attempts)


def reserve_shorts_attempt(
    ledger_path: Path,
    source_key: str,
    *,
    attempt_id: str,
    discovered_attempts: list[dict] | None = None,
) -> None:
    """Check and reserve the instruction pin under the same ledger lock."""
    lock_path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        attempts: list[dict] = []
        if ledger_path.is_file():
            payload = _read_json_object(ledger_path)
            if payload.get("schemaVersion") != ATTEMPT_LEDGER_SCHEMA or payload.get("sourceKey") != source_key:
                raise RuntimeError("쇼츠 NotebookLM 시도 ledger의 schema/source_key가 다릅니다.")
            attempts = list(payload.get("attempts") or [])
        validate_shorts_notebook_retry(
            source_key, [*(discovered_attempts or []), *attempts]
        )
        attempts.append({
            "attempt_id": attempt_id,
            "source_key": source_key,
            "instruction_version": SHORTS_NOTEBOOK_INSTRUCTION_VERSION,
            "instruction_sha256": SHORTS_NOTEBOOK_INSTRUCTION_SHA256,
            "attempt_status": "started",
            "substantive_failure": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        _write_attempt_ledger(ledger_path, source_key, attempts)


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


def validate_head_copy(value: str, *, measure_pixels: bool = True) -> str:
    """Require the owner's spoken two-line first-screen copy shape.

    ``measure_pixels`` is the render constraint rather than the copy contract:
    only the adopted candidate is burned into the video, so only it has to fit
    the 90px safe width.  An alternative that is never rendered is still held to
    every structural rule.
    """
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
    if measure_pixels:
        try:
            validate_headline_pixel_width((first, second))
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
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
    """Require all three ranked candidates to pass the canonical contract."""
    match = HEAD_COPY_HEADER_RE.search(answer or "")
    if not match:
        raise RuntimeError("노트북 응답에 '### 헤드카피라이팅' 절이 없습니다.")
    tail = (answer[match.end():] or "").strip()
    script_heading = SCRIPT_HEADER_RE.search(tail)
    block = tail[:script_heading.start()].strip() if script_heading else tail
    offered = []
    for line in block.splitlines():
        if not line.strip():
            continue
        item = HEAD_COPY_ITEM_RE.match(line)
        if item:
            offered.append(item.group(1))
        elif "/" in line or "／" in line:
            offered.append(line)
    if len(offered) != 3:
        raise RuntimeError("쇼츠 헤드카피 후보가 정확히 3개가 아닙니다.")
    accepted = []
    rejections = []
    for index, raw in enumerate(offered):
        try:
            # The first candidate is the one that gets rendered, so it alone must
            # also survive the 90px width measurement.
            accepted.append(validate_head_copy(raw, measure_pixels=index == 0))
        except RuntimeError as exc:
            rejections.append(str(exc))
    if rejections:
        raise RuntimeError(
            "쇼츠 헤드카피 후보 3개 모두 통과해야 합니다: "
            + "; ".join(dict.fromkeys(rejections))
        )
    if len(set(accepted)) != len(accepted):
        raise RuntimeError("쇼츠 헤드카피 후보가 서로 달라야 합니다.")
    return accepted


def _raw_script_body(answer: str) -> str:
    """Return the heading slice with only outer whitespace removed."""
    match = SCRIPT_HEADER_RE.search(answer or "")
    if not match:
        raise RuntimeError("노트북 응답에 '### 스크립트' 절이 없습니다.")
    body = (answer[match.end():] or "").strip()
    if not body:
        raise RuntimeError("노트북의 스크립트 절이 비어 있습니다.")
    return body


def extract_script(answer: str) -> tuple[str, str]:
    chosen = ""
    match = FORMAT_RE.search(answer or "")
    if match:
        chosen = match.group(1).strip()
    body = _raw_script_body(answer)
    lines = [line.rstrip() for line in body.splitlines()]
    body = "\n".join(line for line in lines if line.strip() not in (".", "·", "-")).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body, chosen


def validate_script_structure(script: str) -> dict:
    """Require one intro and exactly one ordered first-through-fifth body."""
    text = str(script or "")
    matches = []
    for name, pattern in ORDINAL_LINE_PATTERNS.items():
        found = list(pattern.finditer(text))
        if len(found) != 1:
            raise RuntimeError(f"쇼츠 스크립트의 {name} 항목은 정확히 1개여야 합니다.")
        matches.append((name, found[0]))
    positions = [match.start() for _, match in matches]
    if positions != sorted(positions):
        raise RuntimeError("쇼츠 스크립트의 첫째~다섯째 순서가 다릅니다.")
    intro = text[:positions[0]].strip()
    if not intro:
        raise RuntimeError("쇼츠 스크립트에 도입 문장이 없습니다.")
    if SIXTH_RE.search(text) or SEVENTH_OR_LATER_RE.search(text):
        raise RuntimeError("쇼츠 채택 본문에 여섯째 이후 항목이 남아 있습니다.")
    if SCRIPT_META_RE.search(text):
        raise RuntimeError("쇼츠 채택 본문에 타임스탬프·섹션·메타 레이블이 남아 있습니다.")
    return {
        "status": "pass",
        "intro_present": True,
        "first_through_fifth_exactly_once": True,
        "sixth_or_later_present": False,
        "meta_labels_present": False,
    }


def canonicalize_notebooklm_script_layout(script: str) -> tuple[str, dict]:
    """Validate six logical v16 blocks and canonicalize only their separators."""
    text = str(script or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    paragraphs = re.split(r"\n[ \t]*\n", text) if text else []
    boundary_mode = "markdown_blank_lines"
    if len(paragraphs) == 6:
        if any("\n" in paragraph for paragraph in paragraphs):
            raise RuntimeError(
                f"Shorts NotebookLM {SHORTS_NOTEBOOK_INSTRUCTION_VERSION} 스크립트의 "
                "각 논리 문단은 innerText에서 한 줄이어야 합니다."
            )
    else:
        logical_lines = text.splitlines()
        if len(logical_lines) == 6 and all(line.strip() for line in logical_lines):
            paragraphs = logical_lines
            boundary_mode = "innertext_logical_lines"
        else:
            raise RuntimeError(
                f"Shorts NotebookLM {SHORTS_NOTEBOOK_INSTRUCTION_VERSION} 스크립트는 "
                "도입과 첫째~다섯째의 정확히 6개 논리 문단이어야 합니다."
            )
    paragraphs = [paragraph.strip() for paragraph in paragraphs]
    if len(paragraphs) != 6:
        raise RuntimeError(
            f"Shorts NotebookLM {SHORTS_NOTEBOOK_INSTRUCTION_VERSION} 스크립트는 "
            "도입과 첫째~다섯째의 정확히 6개 논리 문단이어야 합니다."
        )
    intro = paragraphs[0]
    if not intro:
        raise RuntimeError(
            f"Shorts NotebookLM {SHORTS_NOTEBOOK_INSTRUCTION_VERSION} 스크립트에 "
            "별도 도입 문단이 없습니다."
        )
    for paragraph, label in zip(paragraphs[1:], NOTEBOOKLM_SCRIPT_PARAGRAPH_LABELS):
        if not paragraph.startswith(label):
            raise RuntimeError(
                f"Shorts NotebookLM {SHORTS_NOTEBOOK_INSTRUCTION_VERSION} 스크립트의 "
                f"{label[:-1]} 항목은 자기 Markdown 문단의 첫 글자로 시작해야 합니다."
            )
    if POST_FIFTH_METADATA_RE.search(paragraphs[-1]):
        raise RuntimeError(
            f"Shorts NotebookLM {SHORTS_NOTEBOOK_INSTRUCTION_VERSION} 스크립트의 "
            "다섯째 문단 뒤에 버전·지침·주석 메타데이터가 남아 있습니다."
        )
    canonical = "\n\n".join(paragraphs)
    structure = validate_script_structure(canonical)
    return canonical, {
        **structure,
        "markdown_paragraph_count": 6,
        "logical_paragraph_count": 6,
        "provider_boundary_mode": boundary_mode,
        "separator_canonicalized": boundary_mode == "innertext_logical_lines",
        "ordinal_paragraphs_start_exactly": True,
        "post_fifth_content_present": False,
    }


def validate_notebooklm_script_layout(script: str) -> dict:
    """Require exactly six v16 logical blocks before any citation cleanup."""
    _canonical, evidence = canonicalize_notebooklm_script_layout(script)
    return evidence


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
    validate_script_structure(text)
    return text


def duration_minutes(seconds: float) -> int:
    if not seconds or seconds <= 0:
        raise RuntimeError("원본 영상 길이를 확인하지 못했습니다.")
    return max(1, int(math.floor(float(seconds) / 60 + 0.5)))


def validate_comment_keyword(value: str) -> str:
    """Comment keyword must be exactly two Hangul characters."""
    keyword = (value or "").strip()
    if not COMMENT_KEYWORD_RE.match(keyword):
        raise RuntimeError("댓글 유도 키워드는 한글 2글자여야 합니다.")
    return keyword


def _comment_keyword_candidates(text: str) -> list[str]:
    candidates = []
    for token in re.findall(r"[가-힣]+", text or ""):
        if len(token) < 2:
            continue
        stripped = COMMENT_KEYWORD_PARTICLE_RE.sub("", token) if len(token) > 2 else token
        if len(stripped) != 2:
            continue
        if stripped in COMMENT_KEYWORD_STOP_WORDS:
            continue
        if stripped.endswith(COMMENT_KEYWORD_VERB_ENDINGS):
            continue
        candidates.append(stripped)
    return candidates


def derive_comment_keyword(
    script: str,
    headline: str = "",
    *,
    override: str | None = None,
) -> str:
    """Pick the video's own two-character keyword for the comment CTA.

    Headline tokens count triple because the headline is the video's promise.
    Ties go to the token that appears first in the script. Falls back to
    DEFAULT_COMMENT_KEYWORD when nothing usable is found. Deterministic: the
    same body and headline always yield the same keyword.
    """
    if override:
        return validate_comment_keyword(override)
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for index, token in enumerate(_comment_keyword_candidates(script)):
        scores[token] = scores.get(token, 0) + 1
        first_seen.setdefault(token, index)
    for token in _comment_keyword_candidates(headline):
        scores[token] = scores.get(token, 0) + 3
        first_seen.setdefault(token, 10_000)
    if not scores:
        return DEFAULT_COMMENT_KEYWORD
    return min(scores, key=lambda token: (-scores[token], first_seen[token]))


def fixed_cta(minutes: int, keyword: str = DEFAULT_COMMENT_KEYWORD) -> str:
    keyword = validate_comment_keyword(keyword)
    return (
        f"{minutes}분 짜리 영상 내용을 모두 정리했습니다.\n\n"
        f"이 자료 궁금하신 분들은 댓글에 {keyword} 남겨주세요."
    )


def require_strong_hook(script: str) -> str:
    first_line = next((line.strip() for line in (script or "").splitlines() if line.strip()), "")
    first_sentence = re.split(r"(?<=[.!?])\s+", first_line, maxsplit=1)[0]
    if not STRONG_HOOK_RE.match(first_sentence):
        raise RuntimeError(
            "쇼츠 첫 문장이 기존 강한 후킹 구조(이 남자/이 여자/이 프로그램)와 다릅니다."
        )
    return first_sentence


def validate_intro_promise(script: str) -> None:
    require_strong_hook(script)
    intro = re.split(r"(?m)^\s*첫째", script, maxsplit=1)[0].strip()
    if len(intro) > 150 or not re.search(r"(?:5|다섯)\s*가지 방법,\s*저장하고 끝까지 보세요!$", intro):
        raise RuntimeError("도입은 150자 이내의 강한 훅과 5가지 방법 예고로 끝나야 합니다.")


def finalize_script(script: str, minutes: int, keyword: str = DEFAULT_COMMENT_KEYWORD) -> str:
    return f"{keep_through_fifth(script)}\n\n{fixed_cta(minutes, keyword)}"


def cta_only_transform_report(
    script: str,
    final: str,
    minutes: int,
    *,
    keyword: str = DEFAULT_COMMENT_KEYWORD,
    provider_answer: str | None = None,
    citation_stripped_answer: str | None = None,
) -> dict:
    """Prove that the NotebookLM body was preserved and only its CTA changed."""
    expected_body = keep_through_fifth(script)
    expected_final = f"{expected_body}\n\n{fixed_cta(minutes, keyword)}"
    if final != expected_final:
        raise RuntimeError("NotebookLM 원고 본문이 CTA 교체 외에 변경되었습니다.")
    provider = str(provider_answer if provider_answer is not None else script)
    stripped = str(
        citation_stripped_answer if citation_stripped_answer is not None else provider
    )
    try:
        provider_body = _raw_script_body(provider)
    except RuntimeError:
        provider_body = provider
    try:
        stripped_body = _raw_script_body(stripped)
    except RuntimeError:
        stripped_body = script
    adopted_from_final = final[: len(expected_body)]
    body_hash = _text_sha256(expected_body)
    if adopted_from_final != expected_body:
        raise RuntimeError("고정 CTA 앞의 채택 본문이 parser 본문과 다릅니다.")
    return {
        "status": "cta_only",
        "notebooklm_body_preserved_exactly": True,
        "content_rewrite_applied": False,
        "removed_notebooklm_cta": True,
        "fixed_cta_applied": True,
        "cta_style": "comment_keyword",
        "comment_keyword": keyword,
        "body_sha256_before": body_hash,
        "body_sha256_after": body_hash,
        "provider_answer_sha256": _text_sha256(provider),
        "provider_script_body_sha256": _text_sha256(provider_body),
        "citation_stripped_answer_sha256": _text_sha256(stripped),
        "citation_stripped_script_body_sha256": _text_sha256(stripped_body),
        "parser_normalized_body_sha256": _text_sha256(script),
        "adopted_body_sha256": body_hash,
        "final_adopted_body_sha256": _text_sha256(adopted_from_final),
        "citation_cleanup_changed_provider_answer": provider != stripped,
        "deterministic_extraction": (
            "provider innerText outer trim -> citation markers/source-list cleanup -> "
            "script-heading slice -> trailing-space and marker-only-line cleanup -> "
            "exact six-block separator canonicalization -> sixth/later and prior CTA suffix cut"
        ),
        "adopted_body_is_exact_final_prefix": True,
    }


def factpack_recovery_report(
    provider_script: str,
    recovered_script: str,
    *,
    factpack_sha256: str,
    source_gate: dict,
) -> dict:
    """Historical recovery is not authorization to rewrite NotebookLM content."""
    raise RuntimeError("팩트팩 재작성 자동 복구는 금지됩니다. 원응답과 실패를 보존하세요.")


def get_video_duration(url: str) -> float:
    import yt_dlp
    from youtube_source_options import source_options

    options = {**source_options(), "skip_download": True}
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
    result["preserve_provider_answer"] = True
    # Keep the existing two-value API for callers, but Shorts no longer uses
    # an API key because the NotebookLM body must not pass through a rewrite.
    return result, ""


FACT_VERIFICATION_FILENAME = "fact-verification.json"


def load_fact_verifications(evidence_dir: str | Path | None) -> list[dict]:
    """Read operator fact checks for claims the source itself makes, if any."""
    if not evidence_dir:
        return []
    path = Path(evidence_dir).expanduser().resolve() / FACT_VERIFICATION_FILENAME
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("verifications") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise RuntimeError("쇼츠 사실확인 파일에 verifications 목록이 없습니다.")
    return entries


def fetch(
    url: str,
    log=print,
    *,
    evidence_dir: str | Path | None = None,
    attempt_ledger_path: str | Path | None = None,
    preserve_authorized_wording: bool = False,
    comment_keyword: str | None = None,
) -> tuple[str, str, int, str, dict, list[str]]:
    source_key = _video_id(url)
    cfg, _unused_api_key = load_shorts_config()
    if evidence_dir:
        cfg["evidence_dir"] = str(Path(evidence_dir).expanduser().resolve())
    default_ledger, source_root = _default_attempt_ledger_path(source_key, evidence_dir)
    ledger_path = (
        Path(attempt_ledger_path).expanduser().resolve()
        if attempt_ledger_path is not None
        else default_ledger
    )
    prior_attempts = load_shorts_attempt_evidence(source_key, ledger_path, source_root)
    attempt_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + f"-{os.getpid()}"
    )
    reserve_shorts_attempt(
        ledger_path,
        source_key,
        attempt_id=attempt_id,
        discovered_attempts=prior_attempts,
    )
    try:
        provider_answer = nlm.fetch_manuscript(url, cfg, log=log)
    except Exception:
        record_shorts_attempt(
            ledger_path,
            source_key,
            attempt_id=attempt_id,
            status="unknown_after_provider_start",
        )
        raise
    record_shorts_attempt(
        ledger_path,
        source_key,
        attempt_id=attempt_id,
        status="provider_response_received",
    )
    try:
        provider_script_body = _raw_script_body(provider_answer)
        validate_notebooklm_script_layout(provider_script_body)
        citation_stripped_answer = nlm._strip_citations(provider_answer)
        head_copies = extract_head_copy_candidates(citation_stripped_answer)
        script, chosen = extract_script(citation_stripped_answer)
        script, _layout = canonicalize_notebooklm_script_layout(script)
        adopted_body = keep_through_fifth(script)
        validate_shorts_verbatim_claims(
            adopted_body,
            preserve_authorized_wording=preserve_authorized_wording,
            fact_verifications=load_fact_verifications(evidence_dir),
        )
        minutes = duration_minutes(get_video_duration(url))
        keyword = derive_comment_keyword(adopted_body, head_copies[0], override=comment_keyword)
        final = f"{adopted_body}\n\n{fixed_cta(minutes, keyword)}"
        transform = cta_only_transform_report(
            script,
            final,
            minutes,
            keyword=keyword,
            provider_answer=provider_answer,
            citation_stripped_answer=citation_stripped_answer,
        )
        validate_intro_promise(final)
        validate_head_copy_connection(head_copies[0], final)
    except Exception:
        record_shorts_attempt(
            ledger_path,
            source_key,
            attempt_id=attempt_id,
            status="substantive_failed",
            substantive_failure=True,
        )
        raise
    record_shorts_attempt(
        ledger_path,
        source_key,
        attempt_id=attempt_id,
        status="passed",
    )
    return final, chosen, minutes, provider_answer, transform, head_copies


def recover(
    url: str,
    stored_answer: str,
    recovered_answer: str,
    recovery_evidence: dict,
    *,
    evidence_dir: str | Path | None = None,
    attempt_ledger_path: str | Path | None = None,
    preserve_authorized_wording: bool = False,
    comment_keyword: str | None = None,
) -> tuple[str, str, int, str, dict, list[str], dict]:
    """Reuse an answer the provider already returned, without a new provider call.

    The retry pin deliberately blocks a second extraction once an attempt has
    failed substantively, so an answer whose capture was mangled by citation
    chrome can only be rescued by re-reading the same response.  Fidelity is
    proven before any gate runs: the re-read must carry the stored answer's
    wording character for character.
    """
    source_key = _video_id(url)
    recovery = validate_recovered_provider_answer(
        source_key=source_key,
        stored_answer=stored_answer,
        recovered_answer=recovered_answer,
        evidence=recovery_evidence,
    )
    default_ledger, source_root = _default_attempt_ledger_path(source_key, evidence_dir)
    ledger_path = (
        Path(attempt_ledger_path).expanduser().resolve()
        if attempt_ledger_path is not None
        else default_ledger
    )
    provider_answer = recovered_answer
    provider_script_body = _raw_script_body(provider_answer)
    validate_notebooklm_script_layout(provider_script_body)
    citation_stripped_answer = nlm._strip_citations(provider_answer)
    head_copies = extract_head_copy_candidates(citation_stripped_answer)
    script, chosen = extract_script(citation_stripped_answer)
    script, _layout = canonicalize_notebooklm_script_layout(script)
    adopted_body = keep_through_fifth(script)
    validate_shorts_verbatim_claims(
        adopted_body,
        preserve_authorized_wording=preserve_authorized_wording,
        fact_verifications=load_fact_verifications(evidence_dir),
    )
    minutes = duration_minutes(get_video_duration(url))
    keyword = derive_comment_keyword(adopted_body, head_copies[0], override=comment_keyword)
    final = f"{adopted_body}\n\n{fixed_cta(minutes, keyword)}"
    transform = cta_only_transform_report(
        script,
        final,
        minutes,
        keyword=keyword,
        provider_answer=provider_answer,
        citation_stripped_answer=citation_stripped_answer,
    )
    validate_intro_promise(final)
    validate_head_copy_connection(head_copies[0], final)
    record_shorts_attempt(
        ledger_path,
        source_key,
        attempt_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        + f"-{os.getpid()}-recovery",
        status="recovered_stored_provider_answer",
    )
    recovery = {
        **recovery,
        "stored_answer_sha256": hashlib.sha256(stored_answer.encode("utf-8")).hexdigest(),
        "recovered_answer_sha256": hashlib.sha256(recovered_answer.encode("utf-8")).hexdigest(),
        "source_root": str(source_root),
    }
    return final, chosen, minutes, provider_answer, transform, head_copies, recovery


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NotebookLM 쇼츠 대본 5번까지 + 댓글 유도 CTA")
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--raw-out")
    parser.add_argument("--headline-out")
    parser.add_argument("--evidence-dir")
    parser.add_argument(
        "--comment-keyword",
        help="댓글 유도 키워드(한글 2글자). 생략하면 대본과 헤드카피에서 자동 추출",
    )
    parser.add_argument(
        "--preserve-authorized-wording",
        action="store_true",
        help="원응답의 승인된 과장 표현을 그대로 둔다(정본 lineage 게이트와 동일 기준)",
    )
    parser.add_argument(
        "--recovered-answer",
        help="새 공급자 호출 없이 이미 받은 원응답을 다시 읽어 저장한 파일",
    )
    parser.add_argument(
        "--stored-answer",
        help="--recovered-answer 와 글자 단위로 대조할 기존 저장 원응답 파일",
    )
    parser.add_argument(
        "--recovery-evidence",
        help="--recovered-answer 의 공급자 DOM 재조회 증거 JSON",
    )
    args = parser.parse_args(argv)

    recovery: dict = {}
    if args.recovered_answer or args.stored_answer or args.recovery_evidence:
        missing = [
            name
            for name, value in (
                ("--recovered-answer", args.recovered_answer),
                ("--stored-answer", args.stored_answer),
                ("--recovery-evidence", args.recovery_evidence),
            )
            if not value
        ]
        if missing:
            parser.error("복구 모드에는 " + ", ".join(missing) + " 가 모두 필요합니다.")
        script, chosen, minutes, raw, transform, head_copies, recovery = recover(
            args.url,
            Path(args.stored_answer).expanduser().resolve().read_text(encoding="utf-8"),
            Path(args.recovered_answer).expanduser().resolve().read_text(encoding="utf-8"),
            json.loads(Path(args.recovery_evidence).expanduser().resolve().read_text(encoding="utf-8")),
            evidence_dir=args.evidence_dir,
            preserve_authorized_wording=args.preserve_authorized_wording,
            comment_keyword=args.comment_keyword,
        )
    else:
        script, chosen, minutes, raw, transform, head_copies = fetch(
            args.url,
            evidence_dir=args.evidence_dir,
            preserve_authorized_wording=args.preserve_authorized_wording,
            comment_keyword=args.comment_keyword,
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
        headline_path.write_text("\n".join(f"{index}. " + " / ".join(head_copy_lines(candidate)) for index, candidate in enumerate(head_copies, 1)) + "\n", encoding="utf-8")
    if args.evidence_dir:
        evidence_path = Path(args.evidence_dir).expanduser().resolve()
        evidence_path.mkdir(parents=True, exist_ok=True)
        (evidence_path / "cta-transform.json").write_text(
            json.dumps(transform, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if recovery:
            (evidence_path / "provider-answer-recovery.json").write_text(
                json.dumps(recovery, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    print(f"노트북 포맷: {chosen or '표기 없음'}")
    print("최종 헤드카피: " + " / ".join(head_copy_lines(head_copies[0])))
    print(f"원고 변환: {transform.get('status', '확인 필요')}")
    print(
        f"원본 영상 길이: {minutes}분 / 5번째 항목까지 사용 / "
        f"댓글 유도 CTA 적용 (키워드: {transform.get('comment_keyword')})"
    )
    print(f"쇼츠 나레이션 저장: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
