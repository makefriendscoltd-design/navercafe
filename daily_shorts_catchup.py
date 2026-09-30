"""오늘 검증을 통과한 쇼츠가 모자라면 모자란 만큼만 더 만든다.

정기 제작은 하루 한 번만 돌았다. 그 시각에 맥이 꺼져 있거나 브라우저가 막혀 있으면
그날은 0편으로 끝났고, 실제로 2026-09-25부터 사흘 동안 그렇게 비었다. 그래서 제작을
새벽으로 옮기고, 낮 동안 매시간 이 스크립트를 깨워 부족분을 채운다.

세 가지를 지킨다.

- 오늘 이미 만든 만큼은 다시 만들지 않는다. 목표에서 뺀 개수만 요청한다.
- 앞선 실행이 아직 돌고 있으면 비켜준다. 잠금은 제작기가 쥐고 있고 여기서는 확인만 한다.
- 채웠으면 아무것도 하지 않는다. 매시간 깨어나도 목표를 채운 날에는 조용히 끝난다.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parent
PYTHON = PROJECT / ".venv312/bin/python"
PRODUCER = PROJECT / "shorts_daily_production.py"
LEGACY_PRODUCER = PROJECT / "reference_daily_production.py"
RUN_LOCK = PROJECT / "outputs/reference-daily-production/run.lock"
KST = ZoneInfo("Asia/Seoul")
DAILY_TARGET = 20


def validated_today(today: str | None = None, project: Path = PROJECT) -> list[str]:
    """오늘 만들어졌고 현재 쇼츠 머신 게이트도 통과하는 원본 ID 목록.

    후보 폴더 이름은 원본을 처음 집어온 날짜라서, 며칠 전 폴더를 오늘 완성하는 일이
    흔하다. 오늘 한 일을 세려면 결과물이 생긴 시각을 봐야 한다.
    """
    today = today or datetime.now(KST).strftime("%Y-%m-%d")
    from shorts_daily_production import validated_shorts_root

    source_keys: set[str] = set()
    for path in project.glob("outputs/*/shorts/final.mp4"):
        made = datetime.fromtimestamp(path.stat().st_mtime, KST).strftime("%Y-%m-%d")
        if made == today and validated_shorts_root(path.parent.parent):
            source_keys.add(path.parent.parent.name.rsplit("-", 1)[0])
    return sorted(source_keys)


def rendered_today(today: str | None = None, project: Path = PROJECT) -> int:
    """호환 이름. 파일 존재가 아니라 검증 통과한 고유 원본 수를 돌려준다."""
    return len(validated_today(today, project))


def producer_running() -> bool:
    """제작기가 아직 돌고 있는지 본다. 잠금 파일은 제작기가 쥔다."""
    for producer in (PRODUCER, LEGACY_PRODUCER):
        found = subprocess.run(["pgrep", "-f", str(producer)], capture_output=True, text=True)
        if found.returncode == 0:
            return True
    return False


def plan(target: int = DAILY_TARGET) -> dict:
    done = rendered_today()
    if producer_running():
        return {"action": "skip", "reason": "제작기가 이미 실행 중", "done": done}
    missing = target - done
    if missing <= 0:
        return {"action": "done", "reason": f"오늘 {done}편으로 목표 충족", "done": done}
    return {"action": "run", "missing": missing, "done": done}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=DAILY_TARGET)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    decision = plan(args.target)
    decision["at"] = datetime.now(KST).isoformat(timespec="seconds")
    print(json.dumps(decision, ensure_ascii=False), flush=True)
    if decision["action"] != "run" or args.dry_run:
        return 0

    completed = subprocess.run(
        [str(PYTHON), "-u", str(PRODUCER), "--target", str(args.target)],
        cwd=PROJECT,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
