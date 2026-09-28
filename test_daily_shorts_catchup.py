"""부족분 채우기 시험. 실제 제작은 돌리지 않는다."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import daily_shorts_catchup as catchup


def _short(project: Path, name: str, *, days_ago: int = 0) -> Path:
    path = project / "outputs" / name / "shorts" / "final.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0")
    if days_ago:
        when = (datetime.now() - timedelta(days=days_ago)).timestamp()
        os.utime(path, (when, when))
    return path


def test_counts_by_when_the_file_was_made_not_the_folder_date(tmp_path: Path):
    """후보 폴더 이름은 원본을 집어온 날짜다. 며칠 전 폴더를 오늘 완성하는 일이 흔하다."""
    _short(tmp_path, "aaa-20260101")
    _short(tmp_path, "bbb-20260101")
    _short(tmp_path, "ccc-20260101", days_ago=3)
    assert catchup.rendered_today(project=tmp_path) == 2


def test_plan_asks_only_for_the_shortfall(monkeypatch):
    monkeypatch.setattr(catchup, "rendered_today", lambda: 4)
    monkeypatch.setattr(catchup, "producer_running", lambda: False)
    assert catchup.plan(target=10) == {"action": "run", "missing": 6, "done": 4}


def test_plan_stays_quiet_once_the_target_is_met(monkeypatch):
    monkeypatch.setattr(catchup, "rendered_today", lambda: 10)
    monkeypatch.setattr(catchup, "producer_running", lambda: False)
    assert catchup.plan(target=10)["action"] == "done"


def test_plan_steps_aside_while_a_run_is_in_progress(monkeypatch):
    """매시간 깨어나므로, 돌고 있는 제작기 위에 또 얹으면 노트북 호출이 충돌한다."""
    monkeypatch.setattr(catchup, "rendered_today", lambda: 0)
    monkeypatch.setattr(catchup, "producer_running", lambda: True)
    assert catchup.plan(target=10)["action"] == "skip"


def test_dry_run_never_starts_the_producer(monkeypatch, capsys):
    monkeypatch.setattr(catchup, "rendered_today", lambda: 0)
    monkeypatch.setattr(catchup, "producer_running", lambda: False)

    def explode(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("dry-run에서 제작기를 부르면 안 된다")

    monkeypatch.setattr(catchup.subprocess, "run", explode)
    assert catchup.main(["--dry-run"]) == 0
    assert '"action": "run"' in capsys.readouterr().out
