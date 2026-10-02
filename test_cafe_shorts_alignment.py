import copy
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import cafe_shorts_alignment as alignment


KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=KST)


def entry(source="abcdefghijk", **changes):
    value = {"source_key": source, "status": "pending", "attempts": 0,
             "manifest": f"outputs/{source}-20260901/cafe/06_cafe_manifest.json"}
    value.update(changes)
    return value


def journal(project: Path, source: str, day: str, *, scheduled="2026-09-28T11:00:00+09:00",
            url=None, status="complete",
            journal_source=None, checks=None, provider_id=None):
    url = url or f"https://www.youtube.com/shorts/{source}"
    provider_id = provider_id or url.rsplit("/", 1)[-1]
    path = project / f"outputs/{source}-{day}/shorts/provider/journal.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "status": status, "source_key": journal_source or source,
        "verified": {"shorts_url": url, "provider_id": provider_id, "scheduled_at": scheduled,
                     "checks": {"url": True, "schedule": True} if checks is None else checks},
    }))
    inventory_path = project / "outputs/inventory-anchor-20260928/shorts/inventory-provider-model-test.json"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory = json.loads(inventory_path.read_text()) if inventory_path.exists() else {
        "status": "pass", "captured_at": "2026-09-28T02:00:00Z", "scan_id": "test-scan",
        "channel_id": "UC_TEST_CHANNEL", "rows": []}
    inventory["rows"] = [row for row in inventory["rows"] if row["provider_id"] != provider_id]
    seconds = int(datetime.fromisoformat(scheduled).timestamp())
    inventory["rows"].append({
        "identity": provider_id, "provider_id": provider_id, "status": "scheduled",
        "model_channel_id": inventory["channel_id"],
        "metadata_origin": "studio_provider_row_model",
        "privacy": "VIDEO_PRIVACY_PRIVATE", "draft_status": "DRAFT_STATUS_NONE",
        "published_seconds": "0", "scheduled_raw": {"scheduledPublishings": [{
            "scheduledTimeSeconds": str(seconds), "action": "SCHEDULED_PUBLISHING_ACTION_SET_PUBLIC",
            "status": "SCHEDULED_PUBLISHING_STATUS_SCHEDULED"}]},
    })
    inventory["pages"] = [{"page": 1, "row_count": len(inventory["rows"]), "next_disabled": True}]
    inventory_path.write_text(json.dumps(inventory))
    return path


def queue(*entries):
    return {"publication_mode": "shorts_aligned", "windows": ["12:00"],
            "shorts_inventory_max_age_hours": 24,
            "entries": list(entries)}


def test_mode_is_explicit_opt_in():
    assert alignment.is_shorts_aligned({"publication_mode": "shorts_aligned"})
    assert not alignment.is_shorts_aligned({})


def test_manifest_root_journal_is_preferred_and_inputs_are_read_only(tmp_path):
    row = entry()
    expected = journal(tmp_path, row["source_key"], "20260901")
    before = copy.deepcopy(row)
    proof = alignment.verified_shorts(tmp_path, row)
    assert proof["scheduled_at"] == "2026-09-28T02:00:00+00:00"
    assert proof["url"] == "https://www.youtube.com/shorts/abcdefghijk"
    assert proof["evidence_path"] == str(expected)
    assert row == before


def test_manifest_journal_malformed_or_mismatched_fails_without_stale_fallback(tmp_path):
    row = entry()
    journal(tmp_path, row["source_key"], "20260801")
    journal(tmp_path, row["source_key"], "20260901", journal_source="XXXXXXXXXXX")
    assert alignment.verified_shorts(tmp_path, row) is None


def test_newest_discovered_invalid_journal_blocks_older_valid(tmp_path):
    row = entry(manifest="not/canonical.json")
    journal(tmp_path, row["source_key"], "20260901")
    journal(tmp_path, row["source_key"], "20260902", checks={})
    assert alignment.verified_shorts(tmp_path, row) is None


def test_conflicting_valid_roots_fail_closed_but_identical_roots_are_deterministic(tmp_path):
    row = entry(manifest="not/canonical.json")
    older = journal(tmp_path, row["source_key"], "20260901")
    newer = journal(tmp_path, row["source_key"], "20260902")
    assert alignment.verified_shorts(tmp_path, row)["evidence_path"] == str(newer)
    journal(tmp_path, row["source_key"], "20260901",
            url="https://www.youtube.com/shorts/ZZZZZZZZZZZ")
    assert alignment.verified_shorts(tmp_path, row) is None


def test_malformed_url_naive_time_and_empty_checks_are_rejected(tmp_path):
    for index, changes in enumerate((
        {"url": "https://youtu.be/ABCDEFGHIJK"},
        {"scheduled": "2026-09-28T11:00:00"},
        {"checks": {}},
    )):
        source = f"abcdefghi{index}k"
        row = entry(source)
        journal(tmp_path, source, "20260901", **changes)
        assert alignment.verified_shorts(tmp_path, row) is None


def test_journal_provider_id_must_equal_url_id(tmp_path):
    row = entry()
    journal(tmp_path, row["source_key"], "20260901", provider_id="ZZZZZZZZZZZ")
    assert alignment.verified_shorts(tmp_path, row) is None


def test_legacy_receipt_requires_exact_source_manifest_and_sealed_checks(tmp_path):
    row = entry()
    root = tmp_path / f"outputs/{row['source_key']}-20260901"
    manifest = root / "cafe/06_cafe_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"source_key": row["source_key"]}))
    receipt = root / "shorts/provider/provider_scheduling_evidence.json"
    receipt.parent.mkdir(parents=True)
    provider_id = "ZZZZZZZZZZZ"
    base = {
        "status": "scheduled_verified", "sourceKey": row["source_key"],
        "providerVideoId": provider_id,
        "providerUrl": f"https://www.youtube.com/shorts/{provider_id}",
        "scheduledAt": "2026-09-28T11:00:00+09:00", "gates": {"sealed": True},
        "finalDirectReverify": {
            "status": "PASS", "source_key": row["source_key"],
            "provider_video_id": provider_id,
            "provider_url": f"https://www.youtube.com/shorts/{provider_id}",
            "checks": {"provider_id_exact": True},
        },
    }
    receipt.write_text(json.dumps(base))
    journal(tmp_path, row["source_key"], "20260801",
            url=f"https://www.youtube.com/shorts/{provider_id}")
    # Remove the unrelated modern receipt while retaining its inventory fixture.
    (tmp_path / f"outputs/{row['source_key']}-20260801/shorts/provider/journal.json").unlink()
    assert alignment.verified_shorts(tmp_path, row)["provider_id"] == provider_id
    manifest.write_text(json.dumps({"source_key": "XXXXXXXXXXX"}))
    assert alignment.verified_shorts(tmp_path, row) is None
    manifest.write_text(json.dumps({"source_key": row["source_key"]}))
    base["sourceKey"] = None
    receipt.write_text(json.dumps(base))
    assert alignment.verified_shorts(tmp_path, row) is None


def test_aligned_entries_orders_past_then_future_and_ignores_cafe_dates(tmp_path):
    past, future = entry("past_______"), entry("future_____")
    journal(tmp_path, past["source_key"], "20260901", scheduled="2026-09-20T20:00:00+09:00")
    journal(tmp_path, future["source_key"], "20260901", scheduled="2026-10-20T20:00:00+09:00")
    past["not_before"] = "2099-01-01T00:00:00+09:00"
    future["planned_publish_at"] = "2000-01-01T00:00:00+09:00"
    assert alignment.aligned_entries(tmp_path, queue(future, past), NOW) == [past, future]


def test_pending_and_failed_retry_rules_and_terminal_exclusions(tmp_path):
    rows = [
        entry("ready______"),
        entry("attempted__", attempts=1),
        entry("retry_due__", status="failed", next_eligible_at=NOW.isoformat()),
        entry("retry_wait_", status="failed", next_eligible_at="2026-09-29T12:00:00+09:00"),
        entry("blocked____", status="blocked"),
        entry("reconcile__", status="reconcile_required"),
        entry("published__", status="published"),
        entry("url_done___", published_url="https://cafe.naver.com/westudyssat/1"),
        entry("no_retry___", do_not_retry=True),
    ]
    for index, row in enumerate(rows):
        journal(tmp_path, row["source_key"], "20260901",
                scheduled=f"2026-09-{20 + index:02d}T11:00:00+09:00")
    assert [row["source_key"] for row in alignment.aligned_entries(tmp_path, queue(*rows), NOW)] == [
        "ready______", "retry_due__"
    ]


def test_alignment_block_prioritizes_reconcile_and_uncertain_reservations(tmp_path):
    ready = entry()
    journal(tmp_path, ready["source_key"], "20260901")
    uncertain = entry("uncertain__", status="reconcile_required")
    assert alignment.alignment_block(tmp_path, queue(ready, uncertain), NOW, ready["source_key"]) == \
        "reconcile_required:uncertain__"
    uncertain["status"] = "failed"
    uncertain["provider_evidence"] = "outputs/u/cafe/provider/13_provider_evidence.json"
    marker = tmp_path / "outputs/u/cafe/provider/12_provider_success_reservation.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}")
    assert alignment.alignment_block(tmp_path, queue(ready, uncertain), NOW, ready["source_key"]) == \
        "reconcile_required:uncertain__"


def test_alignment_block_requires_window_and_exact_first_source(tmp_path):
    first, second = entry("first______"), entry("second_____")
    journal(tmp_path, first["source_key"], "20260901", scheduled="2026-09-20T11:00:00+09:00")
    journal(tmp_path, second["source_key"], "20260901", scheduled="2026-09-21T11:00:00+09:00")
    value = queue(second, first)
    assert alignment.alignment_block(tmp_path, value, NOW, None) == "source_key_required:first______"
    assert alignment.alignment_block(tmp_path, value, NOW, second["source_key"]) == \
        "source_key_not_first_aligned:second_____!=first______"
    assert alignment.alignment_block(tmp_path, value, NOW, first["source_key"]) is None
    assert alignment.alignment_block(tmp_path, value, NOW.replace(hour=13), first["source_key"]) == \
        "outside_publish_window:13"


def test_non_aligned_mode_is_noop(tmp_path):
    assert alignment.aligned_entries(tmp_path, {"entries": [entry()]}, NOW) == []
    assert alignment.alignment_block(tmp_path, {"entries": []}, NOW, None) is None


def test_stale_inventory_is_not_eligible(tmp_path):
    row = entry()
    journal(tmp_path, row["source_key"], "20260901")
    inventory = next((tmp_path / "outputs").glob("*/shorts/inventory-provider-model-*.json"))
    value = json.loads(inventory.read_text())
    value["captured_at"] = "2026-09-26T00:00:00Z"
    inventory.write_text(json.dumps(value))
    assert alignment.verified_shorts(tmp_path, row) is not None
    assert alignment.aligned_entries(tmp_path, queue(row), NOW) == []


def test_selection_reads_inventory_once_and_rejects_naive_now(tmp_path, monkeypatch):
    rows = [entry("once_______"), entry("twice______")]
    for row in rows:
        journal(tmp_path, row["source_key"], "20260901")
    original = alignment._inventory
    calls = 0

    def counted(project):
        nonlocal calls
        calls += 1
        return original(project)

    monkeypatch.setattr(alignment, "_inventory", counted)
    assert len(alignment.aligned_entries(tmp_path, queue(*rows), NOW)) == 2
    assert calls == 1
    assert alignment.aligned_entries(tmp_path, queue(*rows), NOW.replace(tzinfo=None)) == []
    assert calls == 1


def test_future_inventory_is_not_eligible(tmp_path):
    row = entry()
    journal(tmp_path, row["source_key"], "20260901")
    inventory = next((tmp_path / "outputs").glob("*/shorts/inventory-provider-model-*.json"))
    value = json.loads(inventory.read_text())
    value["captured_at"] = "2026-09-29T00:00:00Z"
    inventory.write_text(json.dumps(value))
    assert not alignment.inventory_is_fresh(tmp_path, queue(row), NOW)
    assert alignment.aligned_entries(tmp_path, queue(row), NOW) == []


def test_inventory_fresh_public_api(tmp_path):
    row = entry()
    journal(tmp_path, row["source_key"], "20260901")
    assert alignment.inventory_is_fresh(tmp_path, queue(row), NOW)
    assert not alignment.inventory_is_fresh(tmp_path, queue(row), NOW.replace(tzinfo=None))
    assert not alignment.inventory_is_fresh(
        tmp_path, {**queue(row), "shorts_inventory_max_age_hours": 0}, NOW
    )


def test_inventory_cache_reuses_parse_and_detects_stat_change(tmp_path, monkeypatch):
    row = entry()
    journal(tmp_path, row["source_key"], "20260901")
    inventory = next((tmp_path / "outputs").glob("*/shorts/inventory-provider-model-*.json"))
    original = alignment._read_json
    parses = 0

    def counted(path):
        nonlocal parses
        if path == inventory:
            parses += 1
        return original(path)

    monkeypatch.setattr(alignment, "_read_json", counted)
    assert alignment._inventory(tmp_path) is not None
    assert alignment._inventory(tmp_path) is not None
    assert parses == 1
    value = json.loads(inventory.read_text())
    value["scan_id"] = "changed-scan-id"
    inventory.write_text(json.dumps(value))
    assert alignment._inventory(tmp_path) is not None
    assert parses == 2


def test_inventory_rejects_channel_or_metadata_mismatch(tmp_path):
    row = entry()
    journal(tmp_path, row["source_key"], "20260901")
    inventory = next((tmp_path / "outputs").glob("*/shorts/inventory-provider-model-*.json"))
    value = json.loads(inventory.read_text())
    value["rows"][0]["model_channel_id"] = "UC_WRONG"
    inventory.write_text(json.dumps(value))
    assert alignment._inventory(tmp_path) is None
    value["rows"][0]["model_channel_id"] = value["channel_id"]
    value["rows"][0]["metadata_origin"] = "unsealed"
    inventory.write_text(json.dumps(value))
    assert alignment._inventory(tmp_path) is None


def test_deleted_reservation_needs_exact_recorded_reconciliation(tmp_path):
    ready = entry()
    journal(tmp_path, ready["source_key"], "20260901")
    deleted = entry("deleted____", status="blocked", do_not_retry=True,
                    provider_evidence="outputs/d/cafe/provider/13_provider_evidence.json",
                    last_error="source_is_not_longform: 세로 쇼츠 원본. 발행글 articleid 6107 삭제 완료")
    marker = tmp_path / "outputs/d/cafe/provider/12_provider_success_reservation.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"sourceKey": deleted["source_key"], "articleId": "6107"}))
    assert alignment.alignment_block(tmp_path, queue(ready, deleted), NOW, ready["source_key"]) is None
    marker.write_text(json.dumps({"sourceKey": deleted["source_key"], "articleId": "9999"}))
    assert alignment.alignment_block(tmp_path, queue(ready, deleted), NOW, ready["source_key"]) == \
        "reconcile_required:deleted____"
