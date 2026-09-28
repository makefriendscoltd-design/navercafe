import json
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import cafe_manifest_publisher as publisher
import cafe_queue_runner as runner
import cafe_shorts_alignment as alignment
import content_queue_guard as guard


KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 28, 10, 37, tzinfo=KST)


def aligned_queue(entry):
    return {
        "publication_mode": "shorts_aligned",
        "timezone": "Asia/Seoul",
        "windows": ["10:00", "11:00"],
        "retry_interval_hours": 1,
        "entries": [entry],
    }


def entry(**changes):
    value = {
        "source_key": "source_____",
        "status": "pending",
        "attempts": 0,
        "not_before": "2026-09-28T10:00:00+09:00",
        "provider_evidence": "outputs/source/cafe/provider/13_provider_evidence.json",
        "shorts_provider_id": "shorts____1",
        "shorts_status": "public",
        "shorts_scheduled_at": "2026-09-28T09:00:00+09:00",
        "shorts_url": "https://www.youtube.com/shorts/shorts____1",
    }
    value.update(changes)
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_guard_and_runner_use_the_same_aligned_selection(monkeypatch, tmp_path):
    first, second = entry(source_key="first______"), entry(source_key="second_____")
    queue = aligned_queue(second)
    queue["entries"].insert(0, first)
    selected = [second]
    monkeypatch.setattr(guard, "PROJECT", tmp_path)
    monkeypatch.setattr(guard, "aligned_entries", lambda project, value, now: selected)
    monkeypatch.setattr(runner, "aligned_entries", lambda project, value, now: selected)

    assert guard.select_entry(queue, NOW) is second
    assert runner.due_entries(queue, NOW, tmp_path) == [second]


def test_publisher_blocks_when_alignment_block_returns_a_reason(monkeypatch, tmp_path):
    queue = aligned_queue(entry())
    monkeypatch.setattr(publisher, "PROJECT", tmp_path)
    monkeypatch.setattr(publisher, "read_json", lambda path: queue)
    monkeypatch.setattr(alignment, "is_shorts_aligned", lambda value: True)
    monkeypatch.setattr(
        alignment,
        "alignment_block",
        lambda project, value, now, source_key: "wrong_aligned_source",
    )

    with pytest.raises(publisher.CafePublishWindowClosed) as raised:
        publisher.enforce_cafe_publish_window(NOW, source_key="source_____")
    assert raised.value.reason == "wrong_aligned_source"


def test_verified_article_and_both_cards_win_over_nonzero_process_result(tmp_path):
    queue_path = tmp_path / "queue.json"
    original = entry()
    write_json(queue_path, aligned_queue(original))
    evidence = tmp_path / original["provider_evidence"]
    write_json(
        evidence,
        {
            "status": "published_verified",
            "providerUrl": "https://cafe.naver.com/westudyssat/7001",
            "publicVerification": {"status": "verified", "oglinks": 1, "embeds": 1},
        },
    )
    shorts_before = {key: original[key] for key in (
        "shorts_provider_id", "shorts_status", "shorts_scheduled_at", "shorts_url"
    )}

    runner.record(tmp_path, queue_path, original["source_key"], NOW, False, "publisher exited 9")

    saved = runner.load_queue(queue_path)["entries"][0]
    assert saved["status"] == "published"
    assert saved["published_url"] == "https://cafe.naver.com/westudyssat/7001"
    assert {key: saved[key] for key in shorts_before} == shorts_before


def test_uncertain_marker_requires_reconciliation_and_preserves_shorts(tmp_path):
    queue_path = tmp_path / "queue.json"
    original = entry()
    write_json(queue_path, aligned_queue(original))
    evidence = tmp_path / original["provider_evidence"]
    write_json(evidence, {"status": "published", "providerUrl": "https://cafe.naver.com/westudyssat/7002"})
    write_json(evidence.parent / "provider_uncertain_do_not_retry.json", {"status": "uncertain_do_not_retry"})

    runner.record(tmp_path, queue_path, original["source_key"], NOW, False, "uncertain")

    saved = runner.load_queue(queue_path)["entries"][0]
    assert saved["status"] == "reconcile_required"
    assert saved["do_not_retry"] is True
    assert saved["next_eligible_at"] is None
    assert saved["shorts_provider_id"] == original["shorts_provider_id"]
    assert saved["shorts_scheduled_at"] == original["shorts_scheduled_at"]


def test_aligned_failure_retries_at_the_next_clock_hour_and_preserves_shorts(tmp_path):
    queue_path = tmp_path / "queue.json"
    original = entry()
    write_json(queue_path, aligned_queue(original))

    runner.record(tmp_path, queue_path, original["source_key"], NOW, False, "ordinary failure")

    saved = runner.load_queue(queue_path)["entries"][0]
    assert saved["status"] == "failed"
    assert saved["next_eligible_at"] == "2026-09-28T11:00:00+09:00"
    assert saved["shorts_status"] == original["shorts_status"]
    assert saved["shorts_url"] == original["shorts_url"]


def test_run_command_splits_arguments_and_never_uses_a_shell(monkeypatch, tmp_path):
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    result = runner.run_command(tmp_path, "python tool.py --flag 'two words'", 17)

    assert result.returncode == 0
    assert observed["command"] == ["python", "tool.py", "--flag", "two words"]
    assert observed["shell"] is False
    assert observed["cwd"] == tmp_path
    assert observed["timeout"] == 17
