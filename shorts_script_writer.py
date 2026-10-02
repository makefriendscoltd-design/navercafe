"""자막을 근거로 쇼츠 원고를 쓴다. NotebookLM을 쓰지 않는다.

지침은 그대로 쓴다. 지금까지 NotebookLM 채팅 설정에 넣어 두던 그 메타프롬프트가
정본이고, 여기서는 그것을 프롬프트 앞머리에 붙여 구독 에이전트에 넘긴다. 즉 바뀌는
것은 "누가 쓰느냐"뿐이고 "무엇을 어떻게 쓰느냐"는 같다.

출력 형식도 같다. `### 헤드카피라이팅` 세 후보와 `### 스크립트` 여섯 문단. 그래야
뒤따르는 검사와 렌더가 그대로 돈다.

증거는 세 가지를 묶는다. 자막 원문 sha, 지침 버전·sha, 생성본 sha. NotebookLM 경로가
공급자 응답에 묶여 있던 자리를 이것이 대신한다.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import content_production_policy as policy
import notebooklm_shorts as scripts

ANSWER_FILENAME = "answer.md"
EVIDENCE_FILENAME = "evidence.json"


class ScriptWriterError(RuntimeError):
    pass


def build_prompt(transcript: str, *, minutes: int, title: str = "", channel: str = "") -> str:
    origin = "\n".join(filter(None, [
        f"원본 제목: {title}" if title else "",
        f"원본 채널: {channel}" if channel else "",
        f"원본 영상 길이: {minutes}분",
    ]))
    return f"""{policy.SHORTS_NOTEBOOK_INSTRUCTION}

---

위 지침대로 아래 영상 하나만 근거로 삼아 헤드카피 3안과 스크립트를 쓴다.
근거는 아래 자막 전문뿐이다. 자막에 없는 사실, 숫자, 경력, 성과를 만들지 않는다.
검색하거나 다른 자료를 찾지 않는다.

{origin}

출력은 지침이 정한 형식 그대로 한다. 다른 말, 머리말, 코드 블록을 붙이지 않는다.

원본 자막 전문:
{transcript}
"""


def _headcopy_block(candidates: list[str]) -> str:
    lines = []
    for index, candidate in enumerate(candidates, 1):
        first, second = scripts.head_copy_lines(candidate)
        lines.append(f"{index}. {first} / {second}")
    return "\n".join(lines)


def _with_validated_headcopy(answer: str) -> str:
    """헤드카피 세 후보를 검사 통과본으로 바꾼다.

    본문은 원고 작성기가 쓰고, 화면 문구는 전용 생성기가 쓴다. 한 번에 다 잘 쓰라고
    요구하면 한 줄이 규칙을 어길 때 원고 전체를 버리게 된다.
    """
    import shorts_headcopy

    body, _ = scripts.extract_script(answer)
    accepted: list[str] = []
    try:
        accepted.extend(shorts_headcopy.write_headcopy(
            body, recent=shorts_headcopy.recent_headcopy()))
    except Exception:  # noqa: BLE001 - 아래에서 원고가 준 후보로 채운다
        pass
    for candidate in _offered_headcopy(answer):
        if len(accepted) >= 3:
            break
        if candidate in accepted:
            continue
        try:
            scripts.validate_head_copy(candidate, measure_pixels=False)
        except RuntimeError:
            continue
        accepted.append(candidate)
    if len(accepted) < 3:
        raise ScriptWriterError(f"쓸 수 있는 헤드카피가 {len(accepted)}개뿐입니다.")

    header = scripts.HEAD_COPY_HEADER_RE.search(answer)
    script_heading = scripts.SCRIPT_HEADER_RE.search(answer)
    if not header or not script_heading:
        raise ScriptWriterError("생성본에 헤드카피 또는 스크립트 절이 없습니다.")
    return (answer[:header.end()] + "\n" + _headcopy_block(accepted[:3]) + "\n\n"
            + answer[script_heading.start():])


def _offered_headcopy(answer: str) -> list[str]:
    """원고 작성기가 내놓은 후보. 형식이 맞는 줄만 거둔다."""
    import re

    header = scripts.HEAD_COPY_HEADER_RE.search(answer)
    script_heading = scripts.SCRIPT_HEADER_RE.search(answer)
    if not header or not script_heading:
        return []
    block = answer[header.end():script_heading.start()]
    out = []
    for line in block.splitlines():
        match = re.match(r"^\s*\d+\.\s*(.+?)\s*/\s*(.+?)\s*$", line)
        if match:
            out.append(f"{match.group(1)}\n{match.group(2)}")
    return out


def write_script(transcript: str, *, minutes: int, title: str = "", channel: str = "",
                 transcript_sha256: str = "") -> tuple[str, dict]:
    """원고 본문과 증거를 돌려준다. 형식 검사를 통과한 것만 내보낸다."""
    import subscription_agent

    prompt = build_prompt(transcript, minutes=minutes, title=title, channel=channel)
    try:
        answer = subscription_agent.run(prompt, timeout=600)
    except subscription_agent.SubscriptionAgentError as exc:
        raise ScriptWriterError(str(exc)) from None

    answer = answer.strip()
    if answer.startswith("```"):
        answer = answer.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        scripts.validate_notebooklm_script_layout(scripts._raw_script_body(answer))
    except RuntimeError as exc:
        raise ScriptWriterError(f"생성본이 형식 검사를 통과하지 못했습니다: {exc}") from None
    answer = _with_validated_headcopy(answer)

    evidence = {
        "writer": "subscription_agent",
        "instruction_version": policy.SHORTS_NOTEBOOK_INSTRUCTION_VERSION,
        "instruction_sha256": policy.SHORTS_NOTEBOOK_INSTRUCTION_SHA256,
        "transcript_sha256": transcript_sha256,
        "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
        "source_minutes": minutes,
        "written_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return answer, evidence


def write_to(out_dir: Path, transcript_evidence: dict) -> dict:
    """자막 증거를 받아 원고를 쓰고 `answer.md`와 증거를 남긴다."""
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript = Path(transcript_evidence["transcript"]).read_text(encoding="utf-8")
    minutes = int(transcript_evidence.get("source_minutes")
                  or round(float(transcript_evidence.get("spoken_seconds") or 0) / 60))
    if minutes <= 0:
        raise ScriptWriterError("원본 길이를 알 수 없습니다.")
    answer, evidence = write_script(
        transcript, minutes=minutes,
        title=str(transcript_evidence.get("title") or ""),
        channel=str(transcript_evidence.get("channel") or ""),
        transcript_sha256=str(transcript_evidence.get("transcript_sha256") or ""))
    (out_dir / ANSWER_FILENAME).write_text(answer, encoding="utf-8")
    evidence["answer"] = str(out_dir / ANSWER_FILENAME)
    evidence["transcript"] = transcript_evidence["transcript"]
    (out_dir / EVIDENCE_FILENAME).write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captions_dir", help="shorts_caption_source 가 만든 폴더")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    transcript_evidence = json.loads(
        (Path(args.captions_dir) / "evidence.json").read_text(encoding="utf-8"))
    evidence = write_to(Path(args.out), transcript_evidence)
    print(json.dumps(evidence, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
