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
    (path.parent / "editorial_review.json").write_text('{"status":"pass"}')
    if days_ago:
        when = (datetime.now() - timedelta(days=days_ago)).timestamp()
        os.utime(path, (when, when))
    return path


def test_counts_canonical_and_review_variants_from_production_source_of_truth(tmp_path: Path,
                                                                              monkeypatch):
    """Catchup은 수정 후보까지 세는 제작기 정본과 별도 집계 규칙을 갖지 않는다."""
    import shorts_daily_production
    calls = []
    monkeypatch.setattr(shorts_daily_production, "validated_today",
                        lambda today, project: calls.append((today, project)) or
                        {"canonical-source", "review-variant-source"})
    assert catchup.validated_today("2026-09-30", tmp_path) == [
        "canonical-source", "review-variant-source"]
    assert calls == [("2026-09-30", tmp_path)]
    assert catchup.rendered_today("2026-09-30", tmp_path) == 2


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


def test_catchup_calls_shorts_only_target_runner(monkeypatch):
    monkeypatch.setattr(catchup, "rendered_today", lambda: 3)
    monkeypatch.setattr(catchup, "producer_running", lambda: False)
    calls = []
    monkeypatch.setattr(catchup.subprocess, "run", lambda args, cwd=None: (
        calls.append((args, cwd)) or type("Done", (), {"returncode": 0})()))
    assert catchup.main(["--target", "20"]) == 0
    assert calls[0][0][-2:] == ["--target", "20"]
    assert calls[0][0][2].endswith("shorts_daily_production.py")
