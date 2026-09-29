"""화면 헤드카피를 대본에서 직접 쓴다.

지금까지는 노트북이 준 후보 3개 중에서 골랐다. 그 3개가 이미 틀에 박혀 있어서
무엇을 고르든 `모르면 진짜 손해입니다!`, `상상 못한 팀 자동화!` 같은 문구가 됐다.
어느 영상에 붙여도 말이 되는 문장은 카피가 아니다.

그래서 대본을 근거로 새로 쓴다. 기준은 이 채널에서 실제로 잘 나온 영상들이다.

    포브스 선정 사업가의 / AI 직원 프롬프트 5가지      (34,895회)
    100명 직원 다 짜름 / 2026년 가장 값진 스킬          (12,278회)

둘 다 첫 줄이 영상에서 벌어진 일이다. 질문도 감탄도 아니다. 반대로 조회수가 낮은
쪽은 화면만 봐서는 무슨 영상인지 알 수 없다.

쓰는 것은 에이전트가 하고, 지킬 수 있는지는 코드가 검사한다. 글자 수, 90px 실측 폭,
숫자 표기, 도입 중복은 기존 검사기를 그대로 쓴다. 에이전트가 막히거나 결과가 검사를
못 넘기면 노트북 후보로 돌아간다 - 헤드카피 때문에 제작을 멈추지는 않는다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import notebooklm_shorts as scripts
from headcopy_digits import to_digits

MAX_LINE_CHARS = 18
GOOD_EXAMPLES = (
    ("포브스 선정 사업가의", "AI 직원 프롬프트 5가지"),
    ("100명 직원 다 짜름", "2026년 가장 값진 스킬"),
    ("3시간을 2분으로", "클로드 업무 자동화"),
)


# 음성이 도입에서 말하는 문구, 그리고 어느 영상에 붙여도 말이 되는 빈 문구.
SPOKEN_BY_VOICE = ("미쳤습니다", "대박입니다", "천재입니다")
EMPTY_PHRASES = ("모르면 손해", "모르면 진짜", "상상 못한", "아직도 그냥", "충격")


class HeadcopyError(RuntimeError):
    pass


def _prompt(script: str, recent: list[str]) -> str:
    examples = "\n".join(f"- {first} / {second}" for first, second in GOOD_EXAMPLES)
    avoid = "\n".join(f"- {line}" for line in recent[:10]) or "- (없음)"
    return f"""너는 한국어 유튜브 쇼츠의 화면 헤드카피를 쓴다. 아래 대본으로 후보 3개를 써라.

헤드카피는 영상 위쪽에 큰 글씨로 박히는 두 줄이다. 음성이 하는 말을 그대로 옮기는
자리가 아니라, 화면만 보고도 무슨 일이 벌어졌는지 알게 하는 자리다.

잘 나온 실제 사례:
{examples}

규칙:
- 두 줄. 각 줄 공백 포함 {MAX_LINE_CHARS}자 이내. 짧을수록 좋다.
- 첫 줄은 대본에서 실제로 벌어진 일을 쓴다. 전후 대비가 있으면 그대로 쓴다(3시간을 2분으로).
  대비가 없으면 결과나 규모를 쓴다(직원 100명 다 내보냄).
- 둘째 줄은 그래서 무엇을 얻는지 또는 무엇에 대한 것인지 쓴다.
- 숫자는 아라비아 숫자로 쓴다. 3시간, 2분, 100명, 1300만 원.
- 대본에 없는 숫자·성과·경력을 지어내지 않는다.
- `미쳤습니다`, `대박입니다`, `천재입니다`를 쓰지 않는다. 음성이 이미 말한다.
- `아직도 ~하나요?`, `~하면 손해!`, `모르면 손해`, `상상 못한`처럼 아무 영상에나 붙는
  문구를 쓰지 않는다. 이 영상에만 해당하는 말을 써라.
- `팁`, `방법`, `정리`, `노하우`로 끝내지 않는다.
- 세 후보는 서로 다른 각도여야 한다. 어미만 바꾼 같은 문장 세 개는 안 된다.

최근에 이미 쓴 문구(겹치지 마라):
{avoid}

JSON 하나만 출력한다:
{{"candidates":[{{"line1":"첫 줄","line2":"둘째 줄"}},{{"line1":"","line2":""}},{{"line1":"","line2":""}}]}}

대본:
{script}
"""


def _validated(pairs: list[tuple[str, str]], script: str) -> list[str]:
    """검사를 통과한 후보만, 통과한 순서대로 돌려준다."""
    accepted = []
    for first, second in pairs:
        candidate = to_digits(f"{first}\n{second}")
        flat = candidate.replace("\n", " ")
        if any(word in flat for word in SPOKEN_BY_VOICE + EMPTY_PHRASES):
            continue
        try:
            scripts.validate_head_copy(candidate, measure_pixels=True)
            scripts.validate_head_copy_connection(candidate, script)
        except RuntimeError:
            continue
        if candidate not in accepted:
            accepted.append(candidate)
    return accepted


def write_headcopy(script: str, *, recent: list[str] | None = None) -> list[str]:
    """대본으로 헤드카피 후보를 쓴다. 검사를 통과한 것만 돌려준다."""
    import subscription_agent

    try:
        answer = subscription_agent.run_json(_prompt(script, recent or []), timeout=300)
    except subscription_agent.SubscriptionAgentError as exc:
        raise HeadcopyError(str(exc)) from None
    pairs = []
    for item in answer.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        first = str(item.get("line1") or "").strip()
        second = str(item.get("line2") or "").strip()
        if first and second:
            pairs.append((first, second))
    accepted = _validated(pairs, script)
    if not accepted:
        raise HeadcopyError(f"검사를 통과한 후보가 없습니다: {pairs[:3]}")
    return accepted


def recent_headcopy(limit: int = 10, project: Path | None = None) -> list[str]:
    """최근 후보들이 화면에 쓴 첫 줄. 같은 문구를 다시 쓰지 않으려고 본다."""
    project = project or Path(__file__).resolve().parent
    rows = []
    for path in project.glob("outputs/*/shorts/06_headcopy_candidates.txt"):
        try:
            head = path.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            continue
        match = re.match(r"^\d+\.\s*(.+?)\s*/\s*(.+)$", head.strip())
        if match:
            rows.append((path.stat().st_mtime, f"{match.group(1)} / {match.group(2)}"))
    rows.sort(reverse=True)
    return [line for _, line in rows[:limit]]


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", help="후보의 shorts 폴더 경로")
    parser.add_argument("--write", action="store_true", help="후보 파일에 실제로 적는다")
    args = parser.parse_args(argv)
    recent = recent_headcopy()
    for raw in args.roots:
        root = Path(raw)
        script = (root / "07_script_final.txt").read_text(encoding="utf-8").strip()
        try:
            heads = write_headcopy(script, recent=recent)
        except HeadcopyError as exc:
            print(json.dumps({"root": raw, "status": "fail", "error": str(exc)[:200]},
                             ensure_ascii=False), flush=True)
            continue
        shown = [" / ".join(scripts.head_copy_lines(head)) for head in heads]
        print(json.dumps({"root": raw, "status": "ok", "candidates": shown},
                         ensure_ascii=False), flush=True)
        if args.write:
            import shorts_new_prepare as prepare

            prepare._write_headcopy_file(root / "06_headcopy_candidates.txt", heads)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
