from __future__ import annotations

import json
import hashlib
from pathlib import Path
from datetime import datetime, timedelta

import shorts_daily_production as daily


def test_validation_requires_acceptance_and_upload_bundle(tmp_path, monkeypatch):
    root = tmp_path / "abcdefghijk-20260930"
    (root / "shorts").mkdir(parents=True)
    monkeypatch.setattr("content_acceptance.check_shorts", lambda candidate: [])
    monkeypatch.setattr("content_production_policy.validate_shorts_render_bundle",
                        lambda final: {"status": "pass"})
    assert daily.validated_shorts_root(root)

    monkeypatch.setattr("content_acceptance.check_shorts", lambda candidate: ["기계 검증 불합격"])
    assert not daily.validated_shorts_root(root)


def test_optional_editorial_review_fails_closed_and_binds_current_script(tmp_path, monkeypatch):
    root = tmp_path / "abcdefghijk-20260930"
    shorts = root / "shorts"
    shorts.mkdir(parents=True)
    script = shorts / "07_script_final.txt"
    script.write_text("현재 대본", encoding="utf-8")
    review = shorts / "editorial_review.json"
    monkeypatch.setattr("content_acceptance.check_shorts", lambda candidate: [])
    monkeypatch.setattr("content_production_policy.validate_shorts_render_bundle",
                        lambda final: {"status": "pass"})

    # Optional: legacy roots without a review retain their existing machine gates.
    assert daily.validated_shorts_root(root)
    review.write_text(json.dumps({"status": "pending", "script_sha256": "x"}))
    assert not daily.validated_shorts_root(root)
    review.write_text(json.dumps({"status": "pass", "script_sha256": "stale"}))
    assert not daily.validated_shorts_root(root)
    review.write_text(json.dumps({"status": "pass", "script_sha256": hashlib.sha256(
        script.read_bytes()).hexdigest()}))
    assert daily.validated_shorts_root(root)


def test_runner_replaces_failures_until_target_is_met(tmp_path, monkeypatch):
    candidates = [{"id": key, "title": key} for key in ("failure0001", "success0001", "success0002")]
    monkeypatch.setattr(daily, "PROJECT", tmp_path)
    monkeypatch.setattr(daily, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(daily, "SHARED_LOCK", tmp_path / "shared.lock")
    monkeypatch.setattr(daily, "validated_today", lambda today: set())
    monkeypatch.setattr(daily.selection, "load_candidates", lambda: candidates)
    monkeypatch.setattr(daily.selection, "select", lambda rows, limit=None: (rows, []))

    def fake_produce(key, today):
        return {"source_key": key, "root": str(tmp_path / key),
                "status": "failed" if key == "failure0001" else "pass", "steps": {}}

    monkeypatch.setattr(daily, "produce_one", fake_produce)
    assert daily.main(["--target", "2", "--workers", "2"]) == 0
    journal = next((tmp_path / "reports").glob("*.jsonl"))
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    attempts = [row for row in rows if "source_key" in row]
    assert {row["source_key"] for row in attempts} == {row["id"] for row in candidates}
    assert rows[-1]["summary"]["validated_today"] == 2
    assert rows[-1]["summary"]["attempted"] == 3


def test_dry_run_needs_no_browser_or_shared_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "SHARED_LOCK", tmp_path / "must-not-exist.lock")
    monkeypatch.setattr(daily, "validated_today", lambda today: set())
    monkeypatch.setattr(daily.selection, "load_candidates", lambda: [])
    monkeypatch.setattr(daily.selection, "select", lambda rows, limit=None: ([], []))
    assert daily.main(["--dry-run"]) == 0
    assert not daily.SHARED_LOCK.exists()


def test_prepared_current_inputs_render_without_prepare(tmp_path, monkeypatch):
    root = tmp_path / "outputs/abcdefghijk-20260930"
    (root / "shorts").mkdir(parents=True)
    monkeypatch.setattr("content_run_state.resumable_root", lambda *args: root)
    monkeypatch.setattr(daily, "prepared_shorts_root", lambda candidate: True)
    monkeypatch.setattr(daily, "validation_problems", lambda candidate: [])
    calls = []
    monkeypatch.setattr(daily, "_run", lambda args, timeout=3600: (calls.append(args) or (True, "ok")))
    result = daily.produce_one("abcdefghijk", "20260930")
    assert result["status"] == "pass"
    assert len(calls) == 1 and calls[0][0] == "shorts_v7_builder.py"


def test_existing_invalid_render_is_never_overwritten(tmp_path, monkeypatch):
    root = tmp_path / "outputs/abcdefghijk-20260930"
    final = root / "shorts/final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"preserve")
    monkeypatch.setattr("content_run_state.resumable_root", lambda *args: root)
    monkeypatch.setattr(daily, "validated_shorts_root", lambda candidate: False)
    monkeypatch.setattr(daily, "validation_problems", lambda candidate: ["기계 검증 불합격"])
    monkeypatch.setattr(daily, "_run", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("기존 불합격 영상을 덮어쓰면 안 된다")))
    result = daily.produce_one("abcdefghijk", "20260930")
    assert result["status"] == "existing_invalid"
    assert final.read_bytes() == b"preserve"


def test_failure_dispositions_block_permanent_and_cool_down_transient(tmp_path):
    now = datetime.now(daily.KST)
    journal = tmp_path / "run.jsonl"
    permanent = {"source_key": "permanent01", "disposition": {"kind": "permanent"}}
    cooling = {"source_key": "transient01", "disposition": {
        "kind": "transient", "retry_after": (now + timedelta(minutes=5)).isoformat()}}
    expired = {"source_key": "expired0001", "disposition": {
        "kind": "transient", "retry_after": (now - timedelta(minutes=5)).isoformat()}}
    journal.write_text("\n".join(json.dumps(row) for row in (permanent, cooling, expired)) + "\n")
    assert daily.active_failure_keys(now, tmp_path) == {"permanent01", "transient01"}

    # A later success clears an earlier transient disposition.
    with journal.open("a") as stream:
        stream.write(json.dumps({"source_key": "transient01", "status": "pass"}) + "\n")
    assert daily.active_failure_keys(now, tmp_path) == {"permanent01"}
