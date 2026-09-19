from __future__ import annotations

import hashlib
import json
import os
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

import youtube_shorts_publisher as publisher


KST = ZoneInfo("Asia/Seoul")
SOURCE_A = "abcDEF12345"
SOURCE_B = "zyxWVU98765"


def make_manifest(root: Path, *, source_key: str = SOURCE_A, journal: Path | None = None) -> tuple[Path, str]:
    video_dir = root / source_key
    video_dir.mkdir(parents=True, exist_ok=True)
    video = video_dir / "final.mp4"
    video.write_bytes(("video-" + source_key).encode("ascii"))
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    short_url = f"https://youtu.be/{source_key}"
    watch_url = f"https://www.youtube.com/watch?v={source_key}"
    manifest = {
        "source_key": source_key,
        "title": f"Exact title for {source_key}",
        "description": f"Original video: {short_url}\nCanonical watch: {watch_url}",
        "final_mp4": str(video),
        "final_mp4_sha256": digest,
        "original_urls": [short_url, watch_url],
        "expected_channel": "나민수 AI",
        "journal": str(journal or video_dir / "provider" / "journal.json"),
    }
    path = video_dir / "provider-manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path, digest


def row(
    identity: str,
    *,
    status: str = "public",
    provider_id: str = "otherVID001",
    title: str = "unrelated",
    description: str = "",
    urls: list[str] | None = None,
    page: int = 1,
    scheduled_at: str | None = None,
    published_at: str | None = None,
    visibility: bool | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "identity": identity,
        "provider_id": provider_id,
        "status": status,
        "title": title,
        "description": description,
        "urls": urls or [],
        "page": page,
        "direct_metadata_inspected": True,
        "visibility_control_present": visibility,
    }
    if status == "scheduled":
        value["scheduled_at"] = scheduled_at or "2026-09-06T11:00:00+09:00"
    if status == "public":
        value["published_at"] = published_at or "2026-09-04T11:00:00+09:00"
    return value


def inventory(
    rows: list[dict[str, Any]],
    *,
    scan_id: str = "scan-1",
    phase: str = "test_scan",
    **overrides: Any,
) -> dict[str, Any]:
    counts = {state: sum(item["status"] == state for item in rows) for state in publisher.STATES}
    value = {
        "status": "pass",
        "pagination_complete": True,
        "terminal_reason": "next_disabled",
        "scanned_states": ["public", "scheduled", "private", "draft"],
        "pages_scanned": max([item.get("page", 1) for item in rows] or [1]),
        "rows": rows,
        "status_counts": counts,
        "captured_at": "2026-09-05T09:00:00+09:00",
        "scan_id": scan_id,
        "account": "u0",
        "headless": True,
        "channel": "나민수 AI",
        "phase": phase,
    }
    value.update(overrides)
    return value


def parse_inventory(
    value: dict[str, Any],
    manifest: publisher.PublishManifest,
    *,
    now: datetime | None = None,
    phase: str = "test_scan",
) -> publisher.ProviderInventory:
    return publisher.ProviderInventory.from_mapping(
        value,
        manifest=manifest,
        now=now or datetime(2026, 9, 5, 9, tzinfo=KST),
        phase=phase,
    )


def test_occupancy_retains_existing_duplicate_slots_for_append_only_planning(tmp_path):
    manifest_path,_=make_manifest(tmp_path); manifest=publisher.load_manifest(manifest_path)
    rows=[row('slot-one',status='scheduled',provider_id='slotVID0001',scheduled_at='2026-09-16T20:00:00+09:00'),
          row('slot-two',status='scheduled',provider_id='slotVID0002',scheduled_at='2026-09-16T20:00:00+09:00')]
    raw=inventory(rows,captured_at='2026-09-15T12:10:00+09:00')
    parsed=parse_inventory(raw,manifest,now=datetime(2026,9,15,12,10,tzinfo=KST))
    occupancy=parsed.occupancy_slots(datetime(2026,9,15,12,10,tzinfo=KST))
    assert [x.isoformat() for x in occupancy]==['2026-09-16T20:00:00+09:00']*2
    assert publisher.policy.plan_shorts_schedule(occupancy,datetime(2026,9,15,12,10,tzinfo=KST)).isoformat()=='2026-09-17T11:00:00+09:00'


class FakeProvider:
    account = "u0"
    headless = True

    def __init__(self, rows: list[dict[str, Any]] | None = None, *, visibility: bool = True):
        self.rows = list(rows or [])
        self.visibility = visibility
        self.log: list[str] = []
        self.scan_count = 0
        self.attach_calls = 0
        self.retry_attach_calls = 0
        self.retry2_attach_calls = 0
        self.schedule_calls = 0
        self.raise_after_attach = False
        self.raise_after_schedule = False
        self.fail_schedule_without_mutation = False
        self.scan_overrides: list[list[dict[str, Any]]] = []
        self.scan_metadata_overrides: list[dict[str, Any]] = []
        self.direct_overrides: dict[str, Any] = {}

    def scan_inventory(self, manifest: publisher.PublishManifest, *, phase: str) -> dict[str, Any]:
        self.log.append(f"scan:{phase}")
        self.scan_count += 1
        if self.scan_overrides:
            self.rows = list(self.scan_overrides.pop(0))
        captured_at = datetime(2026, 9, 5, 9, tzinfo=KST) + timedelta(seconds=self.scan_count)
        evidence = inventory(
            list(self.rows),
            scan_id=f"scan-{self.scan_count}",
            phase=phase,
            captured_at=captured_at.isoformat(),
        )
        if self.scan_metadata_overrides:
            evidence.update(self.scan_metadata_overrides.pop(0))
        return evidence

    def attach_once(self, manifest: publisher.PublishManifest, *, draft_sentinel: str) -> dict[str, Any]:
        self.log.append("attach")
        self.attach_calls += 1
        self.rows.append(
            row(
                "draft-new",
                status="draft",
                provider_id="",
                title=draft_sentinel,
                visibility=self.visibility,
            )
        )
        if self.raise_after_attach:
            raise ConnectionError("response lost")
        return {"status": "attached", "provider_observed_attachment_click_count": 1}

    def attach_retry_once(self, manifest: publisher.PublishManifest, *, draft_sentinel: str) -> dict[str, Any]:
        self.log.append("attach_retry1")
        self.retry_attach_calls += 1
        self.rows.append(
            row("draft-new", status="draft", provider_id="", title=draft_sentinel, visibility=self.visibility)
        )
        return {"status": "attached", "provider_observed_attachment_click_count": 1}

    def attach_retry2_once(self, manifest: publisher.PublishManifest, *, draft_sentinel: str) -> dict[str, Any]:
        self.log.append("attach_retry2")
        self.retry2_attach_calls += 1
        self.rows.append(
            row("draft-new", status="draft", provider_id="", title=draft_sentinel, visibility=self.visibility)
        )
        return {"status": "attached", "provider_observed_attachment_click_count": 1}

    def schedule_once(
        self,
        manifest: publisher.PublishManifest,
        target: publisher.ProviderRow,
        slot: datetime,
    ) -> dict[str, Any]:
        self.log.append("schedule")
        self.schedule_calls += 1
        if not self.visibility:
            raise publisher.ScheduleControlUnavailable(
                "manual remediation: provider has neither a preserved upload flow nor a direct visibility control"
            )
        if not self.fail_schedule_without_mutation:
            self.rows = [item for item in self.rows if item["identity"] != target.identity]
            self.rows.append(
                row(
                    "scheduled-new",
                    status="scheduled",
                    provider_id="newVID00001",
                    title=manifest.title,
                    description=manifest.description,
                    urls=list(manifest.original_urls),
                    scheduled_at=slot.isoformat(),
                    visibility=True,
                )
            )
        if self.raise_after_schedule or self.fail_schedule_without_mutation:
            raise ConnectionError("schedule response lost")
        return {"status": "committed", "provider_observed_schedule_click_count": 1}

    def direct_requery(self, manifest: publisher.PublishManifest, provider_id: str) -> dict[str, Any]:
        self.log.append("direct")
        target = next(item for item in self.rows if item.get("provider_id") == provider_id)
        evidence = {
            "provider_id": provider_id,
            "shorts_url": f"https://www.youtube.com/shorts/{provider_id}",
            "exact_row_count": 1,
            "title": target["title"],
            "list_row_title": target["title"],
            "description": target["description"],
            "original_urls": list(manifest.original_urls),
            "no_kids": True,
            "status": target["status"],
            "scheduled_at": target["scheduled_at"],
            "timezone": "Asia/Seoul",
            "account": "u0",
            "headless": True,
            "channel": manifest.expected_channel,
            "captured_at": "2026-09-05T09:00:06+09:00",
            "query_id": "direct-1",
        }
        evidence.update(self.direct_overrides)
        return evidence


class FakeCrm:
    def __init__(self, event_log: list[str] | None = None):
        self.ledger: dict[str, dict[str, Any]] = {}
        self.emits: list[dict[str, Any]] = []
        self.event_log = event_log
        self.raise_after_record = False

    def lookup(self, dedupe_key: str) -> dict[str, Any] | None:
        if self.event_log is not None:
            self.event_log.append("crm_lookup")
        return self.ledger.get(dedupe_key)

    def emit(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.event_log is not None:
            self.event_log.append("crm_emit")
        receipt = {**payload, "status": "success"}
        self.emits.append(dict(payload))
        self.ledger[payload["dedupe_key"]] = receipt
        if self.raise_after_record:
            raise ConnectionError("receipt lost")
        return receipt


def make_runner(
    provider_port: FakeProvider,
    crm: FakeCrm,
    root: Path,
    digest: str,
    *,
    clock: Any = None,
) -> publisher.YouTubeShortsPublisher:
    return publisher.YouTubeShortsPublisher(
        provider_port,
        crm,
        lock_path=root / "shared.lock",
        local_gate=lambda _video: {"video_sha256": digest},
        clock=clock,
    )


def test_manifest_binds_source_title_description_video_hash_and_local_gate(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    manifest = publisher.load_manifest(manifest_path)
    gate = mock.Mock(return_value={"video_sha256": digest})
    result = publisher.validate_local_candidate(manifest, gate=gate)
    gate.assert_called_once_with(manifest.video)
    assert result["video_sha256"] == digest
    assert manifest.source_key in manifest.canonical_urls[0]
    assert set(manifest.original_urls) == set(manifest.canonical_urls)
    assert all(url in manifest.description for url in manifest.canonical_urls)


@pytest.mark.parametrize(
    "original_urls,description",
    [
        ([f"https://youtu.be/{SOURCE_A}"], f"https://youtu.be/{SOURCE_A}"),
        (
            [f"https://youtu.be/{SOURCE_A}", f"https://youtu.be/{SOURCE_A}"],
            f"https://youtu.be/{SOURCE_A}\nhttps://www.youtube.com/watch?v={SOURCE_A}",
        ),
        (
            [f"https://youtu.be/{SOURCE_A}", f"https://www.youtube.com/watch?v={SOURCE_A}"],
            f"https://youtu.be/{SOURCE_A}",
        ),
    ],
)
def test_manifest_requires_exactly_both_canonical_urls_in_list_and_description(
    original_urls: list[str], description: str, tmp_path: Path
) -> None:
    manifest_path, _digest = make_manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["original_urls"] = original_urls
    payload["description"] = description
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(publisher.ManifestError, match="both canonical source URLs"):
        publisher.load_manifest(manifest_path)


@pytest.mark.parametrize("suffix", ["?tracking=bad", "#fragment", "&tracking=bad"])
def test_manifest_rejects_canonical_urls_that_only_appear_as_a_longer_url_token(
    suffix: str, tmp_path: Path
) -> None:
    manifest_path, _digest = make_manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    short_url, watch_url = payload["original_urls"]
    payload["description"] = f"Original video: {short_url}{suffix}\nCanonical watch: {watch_url}{suffix}"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(publisher.ManifestError, match="both canonical source URLs"):
        publisher.load_manifest(manifest_path)


def test_manifest_rejects_wrong_hash_before_provider_access(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["final_mp4_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    provider_port = FakeProvider()
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    with pytest.raises(publisher.ManifestError, match="hash differs"):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.log == []


@pytest.mark.parametrize(
    "account,headless",
    [("u1", True), ("u0", False)],
)
def test_only_aside_headless_u0_is_accepted(account: str, headless: bool, tmp_path: Path) -> None:
    provider_port = FakeProvider()
    provider_port.account = account
    provider_port.headless = headless
    with pytest.raises(publisher.PublisherSafetyError, match="headless account u0"):
        publisher.YouTubeShortsPublisher(provider_port, FakeCrm(), lock_path=tmp_path / "lock")


@pytest.mark.parametrize("state", ["public", "scheduled", "private", "draft"])
def test_every_duplicate_state_blocks_attachment(state: str, tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_id = "" if state == "draft" else "dupVID00001"
    provider_port = FakeProvider(
        [
            row(
                "duplicate",
                status=state,
                provider_id=provider_id,
                title=f"Exact title for {SOURCE_A}",
                scheduled_at="2026-09-07T11:00:00+09:00" if state == "scheduled" else None,
                visibility=True if state == "draft" else None,
            )
        ]
    )
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    with pytest.raises(publisher.DuplicateFound):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.attach_calls == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("description", f"source={SOURCE_A}"),
        ("description", f"https://youtu.be/{SOURCE_A}"),
        ("description", f"https://www.youtube.com/watch?v={SOURCE_A}"),
    ],
)
def test_duplicate_union_checks_source_key_and_both_canonical_urls(
    field: str, value: str, tmp_path: Path
) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    duplicate = row("duplicate", **{field: value})
    provider_port = FakeProvider([duplicate])
    with pytest.raises(publisher.DuplicateFound):
        make_runner(provider_port, FakeCrm(), tmp_path, digest).run(
            manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST)
        )


def test_multiple_duplicate_rows_are_ambiguous_and_never_mutated(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider(
        [
            row("dup-one", title=f"Exact title for {SOURCE_A}"),
            row("dup-two", description=f"https://youtu.be/{SOURCE_A}"),
        ]
    )
    with pytest.raises(publisher.AmbiguousProviderState, match="verify only"):
        make_runner(provider_port, FakeCrm(), tmp_path, digest).run(
            manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST)
        )
    assert provider_port.attach_calls == 0


def test_incomplete_pagination_or_state_coverage_fails_closed(tmp_path: Path) -> None:
    manifest_path, _digest = make_manifest(tmp_path)
    manifest = publisher.load_manifest(manifest_path)
    base = inventory([])
    base["pagination_complete"] = False
    with pytest.raises(publisher.InventoryError, match="pagination"):
        parse_inventory(base, manifest)
    base = inventory([])
    base["scanned_states"] = ["public", "scheduled", "private"]
    with pytest.raises(publisher.InventoryError, match="public, scheduled, private, and draft"):
        parse_inventory(base, manifest)


def test_metadata_and_status_counts_must_cover_every_row(tmp_path: Path) -> None:
    manifest_path, _digest = make_manifest(tmp_path)
    manifest = publisher.load_manifest(manifest_path)
    direct_missing = row("one")
    direct_missing["direct_metadata_inspected"] = False
    with pytest.raises(publisher.InventoryError, match="direct metadata"):
        parse_inventory(inventory([direct_missing]), manifest)
    bad_counts = inventory([row("one")])
    bad_counts["status_counts"]["public"] = 0
    with pytest.raises(publisher.InventoryError, match="status counts"):
        parse_inventory(bad_counts, manifest)


@pytest.mark.parametrize(
    "field,bad_value,error",
    [
        ("account", "u1", "headless account u0"),
        ("headless", False, "headless account u0"),
        ("channel", "다른 채널", "expected_channel"),
        ("phase", "another_phase", "phase"),
        ("captured_at", "2026-09-05T08:54:59+09:00", "fresh"),
    ],
)
def test_inventory_evidence_is_fresh_and_bound_to_runtime_context_before_mutation(
    field: str, bad_value: Any, error: str, tmp_path: Path
) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider()
    provider_port.scan_metadata_overrides = [{field: bad_value}]
    with pytest.raises(publisher.InventoryError, match=error):
        make_runner(provider_port, FakeCrm(), tmp_path, digest).run(
            manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST)
        )
    assert provider_port.attach_calls == 0


def test_attachment_precommit_requires_fresh_distinct_scan_evidence(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider()
    provider_port.scan_metadata_overrides = [{}, {"scan_id": "scan-1"}]
    with pytest.raises(publisher.InventoryError, match="not distinct"):
        make_runner(provider_port, FakeCrm(), tmp_path, digest).run(
            manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST)
        )
    assert provider_port.attach_calls == 0


def test_schedule_precommit_requires_distinct_scan_evidence_and_never_clicks(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider()
    provider_port.scan_metadata_overrides = [{}, {}, {}, {"scan_id": "scan-3"}]
    with pytest.raises(publisher.InventoryError, match="not distinct"):
        make_runner(provider_port, FakeCrm(), tmp_path, digest).run(
            manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST)
        )
    assert provider_port.attach_calls == 1
    assert provider_port.schedule_calls == 0


@pytest.mark.parametrize(
    "scan_number,expected_attach_calls,expected_schedule_calls",
    [
        (2, 0, 0),
        (3, 1, 0),
        (4, 1, 0),
        (5, 1, 1),
    ],
)
def test_each_precommit_and_post_mutation_scan_rejects_equal_captured_at(
    scan_number: int,
    expected_attach_calls: int,
    expected_schedule_calls: int,
    tmp_path: Path,
) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider()
    equal_to_previous = datetime(2026, 9, 5, 9, tzinfo=KST) + timedelta(
        seconds=scan_number - 1
    )
    provider_port.scan_metadata_overrides = [
        *({} for _ in range(scan_number - 1)),
        {"captured_at": equal_to_previous.isoformat()},
    ]

    with pytest.raises(publisher.InventoryError, match="not later than the prior scan"):
        make_runner(provider_port, FakeCrm(), tmp_path, digest).run(
            manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST)
        )

    assert provider_port.attach_calls == expected_attach_calls
    assert provider_port.schedule_calls == expected_schedule_calls


def test_live_run_resamples_kst_clock_for_long_operation_evidence(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider()
    sampled_times = [
        datetime(2026, 9, 5, 9, 0, 0, tzinfo=KST),
        datetime(2026, 9, 5, 9, 0, 1, tzinfo=KST),
        datetime(2026, 9, 5, 9, 0, 2, tzinfo=KST),
        datetime(2026, 9, 5, 9, 0, 3, tzinfo=KST),
        datetime(2026, 9, 5, 9, 4, 30, tzinfo=KST),
        datetime(2026, 9, 5, 9, 8, 0, tzinfo=KST),
        datetime(2026, 9, 5, 9, 12, 0, tzinfo=KST),
        datetime(2026, 9, 5, 9, 16, 0, tzinfo=KST),
        datetime(2026, 9, 5, 9, 16, 1, tzinfo=KST),
    ]
    provider_port.scan_metadata_overrides = [
        {"captured_at": value.isoformat()} for value in sampled_times[1:3] + sampled_times[4:7]
    ]
    provider_port.direct_overrides = {"captured_at": sampled_times[8].isoformat()}
    clock = mock.Mock(side_effect=sampled_times)

    result = make_runner(provider_port, FakeCrm(), tmp_path, digest, clock=clock).run(
        manifest_path
    )

    assert result["status"] == "complete"
    assert result["provider"]["captured_at"] == sampled_times[8].isoformat()
    assert clock.call_count == len(sampled_times)
    assert provider_port.attach_calls == 1
    assert provider_port.schedule_calls == 1


def test_live_run_replans_when_upload_crosses_the_selected_slot(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider()
    sampled_times = [
        datetime(2026, 9, 5, 10, 59, 50, tzinfo=KST),
        datetime(2026, 9, 5, 10, 59, 51, tzinfo=KST),
        datetime(2026, 9, 5, 10, 59, 52, tzinfo=KST),
        datetime(2026, 9, 5, 10, 59, 53, tzinfo=KST),
        datetime(2026, 9, 5, 11, 0, 1, tzinfo=KST),
        datetime(2026, 9, 5, 11, 0, 2, tzinfo=KST),
        datetime(2026, 9, 5, 11, 0, 3, tzinfo=KST),
        datetime(2026, 9, 5, 11, 0, 4, tzinfo=KST),
        datetime(2026, 9, 5, 11, 0, 5, tzinfo=KST),
    ]
    provider_port.scan_metadata_overrides = [
        {"captured_at": value.isoformat()} for value in sampled_times[1:3] + sampled_times[4:6] + sampled_times[7:8]
    ]
    provider_port.direct_overrides = {"captured_at": sampled_times[8].isoformat()}
    clock = mock.Mock(side_effect=sampled_times)

    result = make_runner(provider_port, FakeCrm(), tmp_path, digest, clock=clock).run(manifest_path)

    assert result["status"] == "complete"
    assert result["provider"]["scheduled_at"] == "2026-09-05T20:00:00+09:00"
    journal = json.loads(publisher.load_manifest(manifest_path).journal.read_text(encoding="utf-8"))
    replans = [item for item in journal["history"] if item["event"] == "slot_replanned_before_schedule"]
    assert len(replans) == 1
    assert replans[0]["previous_slot"] == "2026-09-05T11:00:00+09:00"
    assert replans[0]["reasons"] == ["reserved_slot_elapsed"]
    assert provider_port.schedule_calls == 1


def test_live_run_revalidates_slot_when_upload_crosses_kst_date_boundary(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider()
    sampled_times = [
        datetime(2026, 9, 5, 23, 59, 50, tzinfo=KST),
        datetime(2026, 9, 5, 23, 59, 51, tzinfo=KST),
        datetime(2026, 9, 5, 23, 59, 52, tzinfo=KST),
        datetime(2026, 9, 5, 23, 59, 53, tzinfo=KST),
        datetime(2026, 9, 6, 0, 0, 1, tzinfo=KST),
        datetime(2026, 9, 6, 0, 0, 2, tzinfo=KST),
        datetime(2026, 9, 6, 0, 0, 3, tzinfo=KST),
        datetime(2026, 9, 6, 0, 0, 4, tzinfo=KST),
        datetime(2026, 9, 6, 0, 0, 5, tzinfo=KST),
    ]
    provider_port.scan_metadata_overrides = [
        {"captured_at": value.isoformat()} for value in sampled_times[1:3] + sampled_times[4:6] + sampled_times[7:8]
    ]
    provider_port.direct_overrides = {"captured_at": sampled_times[8].isoformat()}
    clock = mock.Mock(side_effect=sampled_times)

    result = make_runner(provider_port, FakeCrm(), tmp_path, digest, clock=clock).run(manifest_path)

    assert result["provider"]["scheduled_at"] == "2026-09-06T11:00:00+09:00"
    journal = json.loads(publisher.load_manifest(manifest_path).journal.read_text(encoding="utf-8"))
    replans = [item for item in journal["history"] if item["event"] == "slot_replanned_before_schedule"]
    assert len(replans) == 1
    assert replans[0]["previous_slot"] == replans[0]["slot"]
    assert replans[0]["reasons"] == ["kst_date_boundary_crossed"]
    assert provider_port.schedule_calls == 1


def test_explicit_now_remains_fixed_and_never_calls_live_clock(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider()
    clock = mock.Mock(side_effect=AssertionError("fixed-now run sampled the live clock"))

    result = make_runner(provider_port, FakeCrm(), tmp_path, digest, clock=clock).run(
        manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST)
    )

    assert result["status"] == "complete"
    clock.assert_not_called()


def test_slot_planner_includes_weekends_and_enforces_two_per_day_and_five_hours(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    manifest = publisher.load_manifest(manifest_path)
    provider_port = FakeProvider()
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    saturday_full = parse_inventory(
        inventory(
            [
                row("sat-11", status="scheduled", provider_id="satVID00001", scheduled_at="2026-09-05T11:00:00+09:00"),
                row("sat-20", status="scheduled", provider_id="satVID00002", scheduled_at="2026-09-05T20:00:00+09:00"),
            ]
        ),
        manifest,
    )
    assert runner._plan(saturday_full, datetime(2026, 9, 4, 10, tzinfo=KST)) == datetime(2026, 9, 6, 11, tzinfo=KST)
    sunday_one = parse_inventory(
        inventory([row("sun-11", status="scheduled", provider_id="sunVID00001", scheduled_at="2026-09-06T11:00:00+09:00")]),
        manifest,
    )
    assert runner._plan(sunday_one, datetime(2026, 9, 6, 9, tzinfo=KST)) == datetime(2026, 9, 6, 20, tzinfo=KST)
    eighteen = parse_inventory(
        inventory([row("sun-18", status="scheduled", provider_id="sunVID00002", scheduled_at="2026-09-06T18:00:00+09:00")]),
        manifest,
    )
    assert runner._plan(eighteen, datetime(2026, 9, 6, 9, tzinfo=KST)) == datetime(2026, 9, 7, 11, tzinfo=KST)


def test_planner_counts_today_public_releases_and_ignores_old_public_history(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    manifest = publisher.load_manifest(manifest_path)
    runner = make_runner(FakeProvider(), FakeCrm(), tmp_path, digest)
    now = datetime(2026, 9, 5, 9, tzinfo=KST)
    occupied = parse_inventory(
        inventory(
            [
                row("old-close-one", published_at="2026-08-01T11:00:00+09:00"),
                row("old-close-two", published_at="2026-08-01T12:00:00+09:00"),
                row("today-one", published_at="2026-09-05T08:00:00+09:00"),
            ]
        ),
        manifest,
        now=now,
    )
    assert runner._plan(occupied, now) == datetime(2026, 9, 5, 20, tzinfo=KST)
    full = parse_inventory(
        inventory(
            [
                row("today-one", published_at="2026-09-05T01:00:00+09:00"),
                row("today-two", published_at="2026-09-05T08:00:00+09:00"),
            ]
        ),
        manifest,
        now=now,
    )
    assert runner._plan(full, now) == datetime(2026, 9, 6, 11, tzinfo=KST)


@pytest.mark.parametrize(
    "published_at",
    ["not-a-date", "2026-09-05T08:00:00", "2026-09-05T08:00:00+00:00"],
)
def test_public_rows_require_explicit_parseable_kst_published_at(
    published_at: str, tmp_path: Path
) -> None:
    manifest_path, _digest = make_manifest(tmp_path)
    manifest = publisher.load_manifest(manifest_path)
    public = row("public-row")
    public["published_at"] = published_at
    with pytest.raises(publisher.InventoryError, match="published_at"):
        parse_inventory(inventory([public]), manifest)


def test_public_row_accepts_exact_date_only_evidence_and_plans_next_kst_day(
    tmp_path: Path,
) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    manifest = publisher.load_manifest(manifest_path)
    public = row("public-row")
    public.pop("published_at")
    public["published_date"] = "2026-09-05"
    parsed = parse_inventory(inventory([public]), manifest)

    assert parsed.rows[0].published_at is None
    assert parsed.rows[0].published_date.isoformat() == "2026-09-05"
    planned = make_runner(FakeProvider(), FakeCrm(), tmp_path, digest)._plan(
        parsed, datetime(2026, 9, 5, 9, tzinfo=KST)
    )
    assert planned == datetime(2026, 9, 6, 11, tzinfo=KST)


def test_public_row_without_timestamp_or_date_fails_closed(tmp_path: Path) -> None:
    manifest_path, _digest = make_manifest(tmp_path)
    manifest = publisher.load_manifest(manifest_path)
    public = row("public-row")
    public.pop("published_at")
    with pytest.raises(publisher.InventoryError, match="published_date"):
        parse_inventory(inventory([public]), manifest)


def test_happy_path_requeries_precommit_and_orders_provider_before_crm(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    event_log: list[str] = []
    provider_port = FakeProvider()
    provider_port.log = event_log
    crm = FakeCrm(event_log)
    result = make_runner(provider_port, crm, tmp_path, digest).run(
        manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST)
    )
    assert result["status"] == "complete"
    assert event_log == [
        "scan:initial_recovery_or_duplicate_scan",
        "scan:attachment_precommit_requery",
        "attach",
        "scan:post_attachment_recovery_scan",
        "scan:schedule_precommit_requery",
        "schedule",
        "scan:post_schedule_requery",
        "direct",
        "crm_lookup",
        "crm_emit",
    ]
    manifest = publisher.load_manifest(manifest_path)
    journal = json.loads(manifest.journal.read_text(encoding="utf-8"))
    assert journal["attachment"] == {"reservation_count": 1, "provider_observed_click_count": 1}
    assert journal["schedule_commit"] == {"reservation_count": 1, "provider_observed_click_count": 1}
    assert journal["crm"] == {"reservation_count": 1, "provider_observed_count": 1}
    assert journal["status"] == "complete"
    assert set(crm.emits[0]) == {"channel", "campaign", "stage", "status", "count", "dedupe_key"}
    assert not any("http" in str(value) or "?" in str(value) for value in crm.emits[0].values())


def test_complete_journal_is_idempotent_without_provider_or_crm_reentry(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider()
    crm = FakeCrm()
    runner = make_runner(provider_port, crm, tmp_path, digest)
    runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    provider_events = list(provider_port.log)
    result = runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert result["crm"] == "already_recorded"
    assert provider_port.log == provider_events
    assert len(crm.emits) == 1


def test_attachment_ambiguity_never_reattaches_and_recovers_exact_sentinel(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider()
    provider_port.raise_after_attach = True
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    with pytest.raises(publisher.AmbiguousProviderState, match="never reupload"):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.attach_calls == 1
    provider_port.raise_after_attach = False
    result = runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert result["status"] == "complete"
    assert provider_port.attach_calls == 1


def test_schedule_ambiguity_with_provider_commit_verifies_only_and_never_reclicks(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider()
    provider_port.raise_after_schedule = True
    crm = FakeCrm()
    runner = make_runner(provider_port, crm, tmp_path, digest)
    with pytest.raises(publisher.AmbiguousProviderState, match="never reclick"):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.schedule_calls == 1
    provider_port.raise_after_schedule = False
    result = runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert result["status"] == "complete"
    assert provider_port.schedule_calls == 1


def test_schedule_ambiguity_without_commit_is_fenced_from_reclick(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider()
    provider_port.fail_schedule_without_mutation = True
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    with pytest.raises(publisher.AmbiguousProviderState):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    with pytest.raises(publisher.AmbiguousProviderState, match="never reclick"):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.schedule_calls == 1


def test_current_direct_draft_visibility_absence_is_manual_not_retry(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider(visibility=False)
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    with pytest.raises(publisher.ScheduleControlUnavailable, match="manual remediation"):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    with pytest.raises(publisher.ScheduleControlUnavailable):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.attach_calls == 1
    assert provider_port.schedule_calls == 1
    journal = json.loads(publisher.load_manifest(manifest_path).journal.read_text(encoding="utf-8"))
    assert journal["status"] == "blocked_manual_visibility_control_missing"


def test_direct_visibility_absence_does_not_preblock_a_preserved_upload_flow(
    tmp_path: Path,
) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider(visibility=True)
    # The inventory row reports no direct-edit visibility control. The provider
    # port still owns a preserved upload-wizard route and can schedule once.
    provider_port.visibility = True
    original_attach = provider_port.attach_once

    def attach_without_direct_visibility(
        manifest: publisher.PublishManifest, *, draft_sentinel: str
    ) -> dict[str, Any]:
        result = original_attach(manifest, draft_sentinel=draft_sentinel)
        provider_port.rows[-1]["visibility_control_present"] = False
        return result

    provider_port.attach_once = attach_without_direct_visibility  # type: ignore[method-assign]
    result = make_runner(provider_port, FakeCrm(), tmp_path, digest).run(
        manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST)
    )
    assert result["status"] == "complete"
    assert provider_port.attach_calls == 1
    assert provider_port.schedule_calls == 1


def test_atomic_journal_uses_replace_and_cleans_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    original = os.replace
    with mock.patch.object(publisher.os, "replace", wraps=original) as replace:
        publisher._atomic_write_json(path, {"status": "safe"})
    replace.assert_called_once()
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "safe"}
    assert list(tmp_path.glob(".journal.json.*.tmp")) == []


def test_recovery_journal_is_fenced_to_source_and_candidate(tmp_path: Path) -> None:
    shared = tmp_path / "shared-journal.json"
    manifest_a_path, digest_a = make_manifest(tmp_path, source_key=SOURCE_A, journal=shared)
    provider_a = FakeProvider(visibility=False)
    with pytest.raises(publisher.ScheduleControlUnavailable):
        make_runner(provider_a, FakeCrm(), tmp_path, digest_a).run(
            manifest_a_path, now=datetime(2026, 9, 5, 9, tzinfo=KST)
        )
    manifest_b_path, digest_b = make_manifest(tmp_path, source_key=SOURCE_B, journal=shared)
    provider_b = FakeProvider()
    with pytest.raises(publisher.AmbiguousProviderState, match="another source"):
        make_runner(provider_b, FakeCrm(), tmp_path, digest_b).run(
            manifest_b_path, now=datetime(2026, 9, 5, 9, tzinfo=KST)
        )
    assert provider_b.log == []


def test_crm_interruption_reconciles_by_dedupe_without_second_emit(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider()
    crm = FakeCrm()
    crm.raise_after_record = True
    runner = make_runner(provider_port, crm, tmp_path, digest)
    with pytest.raises(publisher.AmbiguousProviderState, match="reconcile by dedupe"):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert len(crm.emits) == 1
    crm.raise_after_record = False
    result = runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert result["crm"] == "reconciled"
    assert len(crm.emits) == 1


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("channel", "wrong"),
        ("campaign", "wrong"),
        ("stage", "wrong"),
        ("status", "failed"),
        ("metric_count", 999),
        ("dedupe_key", "external-wrong"),
    ],
)
def test_crm_reconciliation_requires_the_entire_exact_sent_event(
    field: str, bad_value: Any
) -> None:
    payload = {
        "channel": "youtube_shorts",
        "campaign": "youtube-content-repurpose",
        "stage": "sent",
        "status": "success",
        "count": 1,
        "dedupe_key": "external-correct",
    }
    receipt = {
        "channel": payload["channel"],
        "campaign": payload["campaign"],
        "stage": payload["stage"],
        "status": payload["status"],
        "metric_count": payload["count"],
        "dedupe_key": payload["dedupe_key"],
    }
    receipt[field] = bad_value
    with pytest.raises(publisher.AmbiguousProviderState, match="exact sent event"):
        publisher._validate_crm_receipt(receipt, payload)


def test_two_concurrent_publishers_serialize_crm_dedupe_and_emit_once(tmp_path: Path) -> None:
    manifest_a_path, digest_a = make_manifest(tmp_path / "publisher-a")
    manifest_b_path, digest_b = make_manifest(tmp_path / "publisher-b")

    def seed_pending(manifest_path: Path) -> None:
        manifest = publisher.load_manifest(manifest_path)
        store = publisher.AtomicJournal(manifest.journal, manifest)
        baseline = parse_inventory(inventory([], scan_id=f"seed-{manifest.fingerprint}"), manifest)
        journal = store.create(baseline, datetime(2026, 9, 6, 11, tzinfo=KST))
        journal["verified"] = {"provider_id": "newVID00001", "checks": {"all": True}}
        journal["status"] = "provider_verified_crm_pending"
        store.write(journal)

    seed_pending(manifest_a_path)
    seed_pending(manifest_b_path)

    class BlockingCrm(FakeCrm):
        def __init__(self) -> None:
            super().__init__()
            self.first_emit_started = threading.Event()
            self.release_first_emit = threading.Event()
            self.concurrent_emit_started = threading.Event()
            self._emit_entries = 0
            self._entry_lock = threading.Lock()

        def emit(self, payload: dict[str, Any]) -> dict[str, Any]:
            with self._entry_lock:
                self._emit_entries += 1
                if self._emit_entries == 1:
                    self.first_emit_started.set()
                else:
                    self.concurrent_emit_started.set()
            assert self.release_first_emit.wait(timeout=5)
            return super().emit(payload)

    crm = BlockingCrm()
    provider_a = FakeProvider()
    provider_b = FakeProvider()
    runner_a = make_runner(provider_a, crm, tmp_path, digest_a)
    runner_b = make_runner(provider_b, crm, tmp_path, digest_b)
    second_run_started = threading.Event()
    fixed_now = datetime(2026, 9, 5, 9, tzinfo=KST)

    def run_second() -> dict[str, Any]:
        second_run_started.set()
        return runner_b.run(manifest_b_path, now=fixed_now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(runner_a.run, manifest_a_path, now=fixed_now)
        assert crm.first_emit_started.wait(timeout=5)
        second = executor.submit(run_second)
        assert second_run_started.wait(timeout=5)
        assert not crm.concurrent_emit_started.wait(timeout=0.25)
        crm.release_first_emit.set()
        results = [first.result(timeout=5), second.result(timeout=5)]

    assert {result["crm"] for result in results} == {"emitted", "reconciled"}
    assert len(crm.emits) == 1
    assert provider_a.log == []
    assert provider_b.log == []
    reservation_counts = []
    for manifest_path in (manifest_a_path, manifest_b_path):
        journal = json.loads(publisher.load_manifest(manifest_path).journal.read_text(encoding="utf-8"))
        assert journal["status"] == "complete"
        assert journal["crm"]["provider_observed_count"] == 1
        reservation_counts.append(journal["crm"]["reservation_count"])
    assert sorted(reservation_counts) == [0, 1]


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("shorts_url", "https://youtube.com/shorts/newVID00001"),
        ("exact_row_count", 2),
        ("no_kids", False),
        ("timezone", "UTC"),
        ("scheduled_at", "2026-09-06T12:00:00+09:00"),
        ("original_urls", []),
        ("original_urls", [f"https://youtu.be/{SOURCE_A}"]),
        ("description", f"https://youtu.be/{SOURCE_A}"),
        ("account", "u1"),
        ("headless", False),
        ("channel", "wrong channel"),
        ("captured_at", "2026-09-05T08:54:59+09:00"),
        ("query_id", ""),
    ],
)
def test_direct_requery_requires_every_exact_provider_field(field: str, bad_value: Any, tmp_path: Path) -> None:
    manifest_path, _digest = make_manifest(tmp_path)
    manifest = publisher.load_manifest(manifest_path)
    slot = datetime(2026, 9, 6, 11, tzinfo=KST)
    evidence = {
        "provider_id": "newVID00001",
        "shorts_url": "https://www.youtube.com/shorts/newVID00001",
        "exact_row_count": 1,
        "title": manifest.title,
        "list_row_title": manifest.title,
        "description": manifest.description,
        "original_urls": list(manifest.original_urls),
        "no_kids": True,
        "status": "scheduled",
        "scheduled_at": slot.isoformat(),
        "timezone": "Asia/Seoul",
        "account": "u0",
        "headless": True,
        "channel": manifest.expected_channel,
        "captured_at": "2026-09-05T09:00:00+09:00",
        "query_id": "direct-test-1",
    }
    evidence[field] = bad_value
    with pytest.raises(publisher.AmbiguousProviderState, match="direct provider requery failed"):
        publisher.validate_direct_evidence(
            evidence,
            manifest,
            "newVID00001",
            slot,
            now=datetime(2026, 9, 5, 9, tzinfo=KST),
        )


@pytest.mark.parametrize(
    "field,transform",
    [
        ("title", lambda value: value.replace(" ", "")),
        ("description", lambda value: value.replace("\n", "")),
        ("description", lambda value: value.replace(" ", "")),
    ],
)
def test_direct_requery_rejects_semantic_whitespace_changes(
    field: str, transform: Any, tmp_path: Path
) -> None:
    manifest_path, _digest = make_manifest(tmp_path)
    manifest = publisher.load_manifest(manifest_path)
    slot = datetime(2026, 9, 6, 11, tzinfo=KST)
    evidence = {
        "provider_id": "newVID00001",
        "shorts_url": "https://www.youtube.com/shorts/newVID00001",
        "exact_row_count": 1,
        "title": manifest.title,
        "list_row_title": manifest.title,
        "description": manifest.description,
        "original_urls": list(manifest.original_urls),
        "no_kids": True,
        "status": "scheduled",
        "scheduled_at": slot.isoformat(),
        "timezone": "Asia/Seoul",
        "account": "u0",
        "headless": True,
        "channel": manifest.expected_channel,
        "captured_at": "2026-09-05T09:00:00+09:00",
        "query_id": "direct-semantic-whitespace",
    }
    evidence[field] = transform(evidence[field])

    with pytest.raises(publisher.AmbiguousProviderState, match="direct provider requery failed"):
        publisher.validate_direct_evidence(
            evidence,
            manifest,
            "newVID00001",
            slot,
            now=datetime(2026, 9, 5, 9, tzinfo=KST),
        )


def test_direct_requery_allows_only_provider_safe_unicode_and_line_endings(tmp_path: Path) -> None:
    manifest_path, _digest = make_manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["title"] = "Cafe\u0301 title " + SOURCE_A
    payload["description"] = (
        f"Original\u00a0video: https://youtu.be/{SOURCE_A}\n"
        f"Canonical watch: https://www.youtube.com/watch?v={SOURCE_A}"
    )
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    manifest = publisher.load_manifest(manifest_path)
    slot = datetime(2026, 9, 6, 11, tzinfo=KST)
    evidence = {
        "provider_id": "newVID00001",
        "shorts_url": "https://www.youtube.com/shorts/newVID00001",
        "exact_row_count": 1,
        "title": "Caf\u00e9\u200b title " + SOURCE_A,
        "list_row_title": "Caf\u00e9\u200b title " + SOURCE_A,
        "description": manifest.description.replace("\u00a0", " ").replace("\n", "\r\n"),
        "original_urls": list(manifest.original_urls),
        "no_kids": True,
        "status": "scheduled",
        "scheduled_at": slot.isoformat(),
        "timezone": "Asia/Seoul",
        "account": "u0",
        "headless": True,
        "channel": manifest.expected_channel,
        "captured_at": "2026-09-05T09:00:00+09:00",
        "query_id": "direct-provider-safe-normalization",
    }

    verified = publisher.validate_direct_evidence(
        evidence,
        manifest,
        "newVID00001",
        slot,
        now=datetime(2026, 9, 5, 9, tzinfo=KST),
    )

    assert all(verified["checks"].values())


def test_direct_requery_context_mismatch_fences_crm(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    provider_port = FakeProvider()
    provider_port.direct_overrides = {"channel": "다른 채널"}
    crm = FakeCrm()
    with pytest.raises(publisher.AmbiguousProviderState, match="direct provider requery failed"):
        make_runner(provider_port, crm, tmp_path, digest).run(
            manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST)
        )
    assert crm.emits == []


def test_precommit_requery_duplicate_stops_before_attachment(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    duplicate = row("race", description=f"https://youtu.be/{SOURCE_A}")
    provider_port = FakeProvider()
    provider_port.scan_overrides = [[], [duplicate]]
    with pytest.raises(publisher.DuplicateFound, match="precommit"):
        make_runner(provider_port, FakeCrm(), tmp_path, digest).run(
            manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST)
        )
    assert provider_port.attach_calls == 0
    assert provider_port.log == [
        "scan:initial_recovery_or_duplicate_scan",
        "scan:attachment_precommit_requery",
    ]


def test_source_independence_does_not_treat_another_source_as_duplicate(tmp_path: Path) -> None:
    manifest_a_path, _digest_a = make_manifest(tmp_path, source_key=SOURCE_A)
    manifest_b_path, _digest_b = make_manifest(tmp_path, source_key=SOURCE_B)
    manifest_a = publisher.load_manifest(manifest_a_path)
    manifest_b = publisher.load_manifest(manifest_b_path)
    provider_inventory = parse_inventory(
        inventory([row("source-a", title=manifest_a.title, description=manifest_a.description)]),
        manifest_a,
    )
    assert len(provider_inventory.matches(manifest_a)) == 1
    assert provider_inventory.matches(manifest_b) == ()


def test_an_in_flight_upload_is_named_rather_than_blocking_the_scan():
    """One row still transferring must not make the whole inventory unreadable."""
    assert publisher.UPLOADING_STATE not in publisher.STATES
    assert publisher.UPLOADING_STATE in publisher.ROW_STATES


def test_an_uploading_row_reserves_no_slot_and_is_never_a_target(monkeypatch):
    """It has no visibility and no schedule, so planning must ignore it."""
    row = publisher.ProviderRow.from_mapping({
        "identity": "rIi5_FfFuhg", "provider_id": "rIi5_FfFuhg",
        "status": publisher.UPLOADING_STATE, "title": "final", "description": "",
        "urls": [], "page": 1, "metadata_origin": "studio_provider_row_model",
        "direct_metadata_inspected": True,
    })
    assert row.status == publisher.UPLOADING_STATE
    assert row.scheduled_at is None
    assert row.published_at is None


def test_a_row_with_no_readable_state_at_all_still_fails():
    with pytest.raises(publisher.InventoryError):
        publisher.ProviderRow.from_mapping({
            "identity": "rIi5_FfFuhg", "provider_id": "rIi5_FfFuhg",
            "status": "처리중", "title": "final", "description": "",
            "urls": [], "page": 1, "metadata_origin": "studio_provider_row_model",
            "direct_metadata_inspected": True,
        })


def test_a_sentinel_title_in_the_list_blocks_even_when_the_form_reads_correctly(tmp_path: Path) -> None:
    """The form shows what was typed; only the list shows what the provider stored."""
    manifest_path, _digest = make_manifest(tmp_path)
    manifest = publisher.load_manifest(manifest_path)
    slot = datetime(2026, 9, 6, 11, tzinfo=KST)
    evidence = {
        "provider_id": "newVID00001",
        "shorts_url": "https://www.youtube.com/shorts/newVID00001",
        "exact_row_count": 1,
        "title": manifest.title,
        "list_row_title": manifest.draft_sentinel,
        "description": manifest.description,
        "original_urls": list(manifest.original_urls),
        "no_kids": True,
        "status": "scheduled",
        "scheduled_at": slot.isoformat(),
        "timezone": "Asia/Seoul",
        "account": "u0",
        "headless": True,
        "channel": manifest.expected_channel,
        "captured_at": datetime(2026, 9, 5, 9, tzinfo=KST).isoformat(),
        "query_id": "q-1",
    }
    with pytest.raises(publisher.AmbiguousProviderState, match="list_row_title"):
        publisher.validate_direct_evidence(
            evidence, manifest, "newVID00001", slot, now=datetime(2026, 9, 5, 9, tzinfo=KST))


def test_a_missing_list_title_is_not_treated_as_verified(tmp_path: Path) -> None:
    """Absent evidence must fail closed, not pass by default."""
    manifest_path, _digest = make_manifest(tmp_path)
    manifest = publisher.load_manifest(manifest_path)
    slot = datetime(2026, 9, 6, 11, tzinfo=KST)
    evidence = {
        "provider_id": "newVID00001",
        "shorts_url": "https://www.youtube.com/shorts/newVID00001",
        "exact_row_count": 1,
        "title": manifest.title,
        "description": manifest.description,
        "original_urls": list(manifest.original_urls),
        "no_kids": True,
        "status": "scheduled",
        "scheduled_at": slot.isoformat(),
        "timezone": "Asia/Seoul",
        "account": "u0",
        "headless": True,
        "channel": manifest.expected_channel,
        "captured_at": datetime(2026, 9, 5, 9, tzinfo=KST).isoformat(),
        "query_id": "q-2",
    }
    with pytest.raises(publisher.AmbiguousProviderState, match="list_row_title"):
        publisher.validate_direct_evidence(
            evidence, manifest, "newVID00001", slot, now=datetime(2026, 9, 5, 9, tzinfo=KST))

ZERO_RECEIPT_FIXTURE = Path(__file__).with_name("fixtures") / "v1-zero-action-receipt.json"


def seed_zero_action_journal(manifest_path: Path, receipt: dict[str, Any]) -> publisher.PublishManifest:
    manifest = publisher.load_manifest(manifest_path)
    now = datetime(2026, 9, 5, 9, tzinfo=KST)
    baseline = publisher.ProviderInventory.from_mapping(
        inventory([], scan_id="baseline", phase="attachment_precommit_requery", captured_at=now.isoformat()),
        manifest=manifest,
        now=now,
        phase="attachment_precommit_requery",
    )
    store = publisher.AtomicJournal(manifest.journal, manifest)
    journal = store.create(baseline, datetime(2026, 9, 6, 11, tzinfo=KST))
    store.reserve(journal, "attachment")
    journal["status"] = "ambiguous_recover_or_verify_only"
    store.event(journal, "attachment_invocation_interrupted")
    provider_dir = manifest.video.parent / "provider"
    provider_dir.mkdir(exist_ok=True)
    (provider_dir / "attachment_invocation.json").write_text(json.dumps({
        "session_marker": "a" * 32,
        "video_sha256": manifest.video_sha256,
        "draft_sentinel": manifest.draft_sentinel,
        "status": "started",
    }), encoding="utf-8")
    (provider_dir / "attachment_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    return manifest


def actual_zero_receipt() -> dict[str, Any]:
    return json.loads(ZERO_RECEIPT_FIXTURE.read_text(encoding="utf-8"))


def test_proven_legacy_zero_action_allows_exactly_one_audited_retry(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    manifest = seed_zero_action_journal(manifest_path, actual_zero_receipt())
    authorize_same_batch_addition(manifest, tmp_path, include_completed=False)
    provider_port = FakeProvider()
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    result = runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert result["status"] == "complete"
    assert provider_port.attach_calls == 0
    assert provider_port.retry_attach_calls == 1
    journal = json.loads(manifest.journal.read_text(encoding="utf-8"))
    assert journal["attachment"]["reservation_count"] == 1
    assert journal["attachment_retry"] == {"reservation_count": 1, "provider_observed_click_count": 1}
    assert sum(x["event"] == "attachment_zero_action_proved" for x in journal["history"]) == 1


@pytest.mark.parametrize("mutation", ["missing", "one", "string_zero", "wrong_hash"])
def test_zero_action_retry_rejects_unsealed_evidence(tmp_path: Path, mutation: str) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    receipt = actual_zero_receipt()
    if mutation == "missing":
        receipt.pop("provider_observed_attachment_click_count")
    elif mutation == "one":
        receipt["provider_observed_attachment_click_count"] = 1
    elif mutation == "string_zero":
        receipt["provider_observed_attachment_click_count"] = "0"
    manifest = seed_zero_action_journal(manifest_path, receipt)
    if mutation == "wrong_hash":
        path = manifest.video.parent / "provider" / "attachment_invocation.json"
        invocation = json.loads(path.read_text())
        invocation["video_sha256"] = "0" * 64
        path.write_text(json.dumps(invocation))
    provider_port = FakeProvider()
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    with pytest.raises(publisher.AmbiguousProviderState):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.retry_attach_calls == 0


def test_zero_action_retry_rejects_inventory_drift(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    seed_zero_action_journal(manifest_path, actual_zero_receipt())
    provider_port = FakeProvider([row("otherVID001")])
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    with pytest.raises(publisher.AmbiguousProviderState, match="authorization is missing"):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.retry_attach_calls == 0


def test_zero_action_retry_reservation_permanently_fences_repeat(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    manifest = seed_zero_action_journal(manifest_path, actual_zero_receipt())
    journal = json.loads(manifest.journal.read_text())
    journal["attachment_retry"] = {"reservation_count": 1, "provider_observed_click_count": 0}
    manifest.journal.write_text(json.dumps(journal))
    provider_port = FakeProvider()
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    with pytest.raises(publisher.AmbiguousProviderState):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.retry_attach_calls == 0


def authorize_same_batch_addition(
    target: publisher.PublishManifest,
    root: Path,
    *,
    provider_id: str = "otherVID001",
    include_completed: bool = True,
) -> Path:
    other_path, _ = make_manifest(root, source_key=SOURCE_B)
    other = publisher.load_manifest(other_path)
    other.journal.parent.mkdir(parents=True, exist_ok=True)
    other.journal.write_text(json.dumps({
        "schema_version": "youtube-shorts-publisher/v1",
        "source_key": other.source_key,
        "manifest_fingerprint": other.fingerprint,
        "final_mp4_sha256": other.video_sha256,
        "status": "complete",
        "verified": {
            "provider_id": provider_id,
            "account": "u0",
            "headless": True,
            "channel": "나민수 AI",
            "checks": {"provider_id": True, "channel": True, "fresh": True},
        },
    }), encoding="utf-8")
    keys = [SOURCE_A, SOURCE_B, "thirdSRC001", "fourthSR001", "fifthSRC001", "sixthSRC001", "sevenSRC001"]
    selection = root / "selection.json"
    selection.write_text(json.dumps({
        "scope": "seven_shorts_then_schedule_naminsoo_append_only",
        "items": [{"source_key": value} for value in keys],
    }), encoding="utf-8")
    journal = json.loads(target.journal.read_text())
    baseline_digest = hashlib.sha256(
        json.dumps(journal["baseline_identities"], separators=(",", ":")).encode()
    ).hexdigest()
    authorization = target.video.parent / "provider" / "zero_action_retry_authorization.json"
    authorization.write_text(json.dumps({
        "schema_version": "zero-action-retry/v1",
        "target_manifest_fingerprint": target.fingerprint,
        "baseline_identities_sha256": baseline_digest,
        "selection_path": str(selection),
        "selection_sha256": hashlib.sha256(selection.read_bytes()).hexdigest(),
        "legacy_invocation_sha256": hashlib.sha256(
            (target.video.parent / "provider" / "attachment_invocation.json").read_bytes()
        ).hexdigest(),
        "legacy_receipt_sha256": hashlib.sha256(
            (target.video.parent / "provider" / "attachment_receipt.json").read_bytes()
        ).hexdigest(),
        "completed_provider_manifests": [str(other_path)] if include_completed else [],
    }), encoding="utf-8")
    return authorization


def seed_two_proven_zero_actions(
    manifest_path: Path,
    *,
    retry1_count: object = 0,
    retry1_hash: str | None = None,
) -> publisher.PublishManifest:
    manifest = seed_zero_action_journal(manifest_path, actual_zero_receipt())
    authorization = authorize_same_batch_addition(manifest, manifest_path.parent, include_completed=False)
    provider_dir = manifest.video.parent / "provider"
    original_invocation = provider_dir / "attachment_invocation.json"
    original_receipt = provider_dir / "attachment_receipt.json"
    marker = "4edad9c9a47b4196b609620de9e0d15b"
    retry1_invocation = provider_dir / "attachment_invocation_retry1.json"
    retry1_receipt = provider_dir / "attachment_receipt_retry1.json"
    retry1_invocation.write_text(json.dumps({
        "session_marker": marker,
        "video_sha256": manifest.video_sha256,
        "draft_sentinel": manifest.draft_sentinel,
        "status": "started",
        "attempt": "retry1",
    }))
    retry1_receipt.write_text(json.dumps({
        "status": "blocked",
        "account": "u0",
        "headless": True,
        "channel": manifest.expected_channel,
        "provider_observed_attachment_click_count": retry1_count,
        "attachment_sha256": retry1_hash or manifest.video_sha256,
        "draft_sentinel": manifest.draft_sentinel,
        "session_marker": marker,
        "attachment_stage": "upload_control_opened",
        "diagnostic": {"dialog_count": 0, "title_count": 0, "files": []},
        "error": "file-input-cardinality:0",
    }))
    journal = json.loads(manifest.journal.read_text())
    journal["attachment_retry"] = {"reservation_count": 1, "provider_observed_click_count": 0}
    journal["status"] = "ambiguous_recover_or_verify_only"
    journal["history"].extend([
        {
            "event": "attachment_zero_action_proved",
            "invocation_sha256": hashlib.sha256(original_invocation.read_bytes()).hexdigest(),
            "receipt_sha256": hashlib.sha256(original_receipt.read_bytes()).hexdigest(),
            "authorization_sha256": hashlib.sha256(authorization.read_bytes()).hexdigest(),
        },
        {"event": "attachment_retry_reserved"},
        {"event": "attachment_retry1_interrupted_no_more_reattach"},
    ])
    manifest.journal.write_text(json.dumps(journal))
    return manifest


def test_two_exact_zero_actions_allow_one_final_retry(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    manifest = seed_two_proven_zero_actions(manifest_path)
    provider_port = FakeProvider()
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    assert runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))["status"] == "complete"
    assert provider_port.retry_attach_calls == 0
    assert provider_port.retry2_attach_calls == 1
    journal = json.loads(manifest.journal.read_text())
    assert journal["attachment_retry2"] == {
        "reservation_count": 1,
        "provider_observed_click_count": 1,
    }
    assert sum(x["event"] == "attachment_retry1_zero_action_proved" for x in journal["history"]) == 1


@pytest.mark.parametrize("mutation", ["missing", "one", "string_zero", "wrong_hash", "target_row"])
def test_retry2_rejects_unsealed_second_zero_action(tmp_path: Path, mutation: str) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    count: object = 0
    if mutation == "missing":
        count = None
    elif mutation == "one":
        count = 1
    elif mutation == "string_zero":
        count = "0"
    manifest = seed_two_proven_zero_actions(
        manifest_path,
        retry1_count=count,
        retry1_hash="0" * 64 if mutation == "wrong_hash" else None,
    )
    if mutation == "missing":
        receipt = manifest.video.parent / "provider" / "attachment_receipt_retry1.json"
        value = json.loads(receipt.read_text())
        value.pop("provider_observed_attachment_click_count")
        receipt.write_text(json.dumps(value))
    provider_port = FakeProvider(
        [row("target-row", status="draft", title=manifest.draft_sentinel)]
        if mutation == "target_row" else []
    )
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    with pytest.raises(publisher.AmbiguousProviderState):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.retry2_attach_calls == 0


def test_retry2_is_permanently_fenced_after_any_prior_reservation(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    manifest = seed_two_proven_zero_actions(manifest_path)
    journal = json.loads(manifest.journal.read_text())
    journal["attachment_retry2"] = {"reservation_count": 1, "provider_observed_click_count": 0}
    manifest.journal.write_text(json.dumps(journal))
    provider_port = FakeProvider()
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    with pytest.raises(publisher.AmbiguousProviderState):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.retry2_attach_calls == 0


def test_zero_action_retry_accepts_only_proven_same_batch_delta_and_replans_slot(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    manifest = seed_zero_action_journal(manifest_path, actual_zero_receipt())
    authorize_same_batch_addition(manifest, tmp_path)
    provider_port = FakeProvider([row(
        "otherVID001",
        status="scheduled",
        provider_id="otherVID001",
        scheduled_at="2026-09-06T11:00:00+09:00",
    )])
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    result = runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert result["status"] == "complete"
    assert provider_port.retry_attach_calls == 1
    journal = json.loads(manifest.journal.read_text())
    replans = [x for x in journal["history"] if x["event"] == "attachment_retry1_slot_replanned"]
    assert replans == [{
        "event": "attachment_retry1_slot_replanned",
        "at": replans[0]["at"],
        "previous_slot": "2026-09-06T11:00:00+09:00",
        "fresh_slot": "2026-09-06T20:00:00+09:00",
    }]


def test_zero_action_retry_allows_unrelated_draft_and_audits_inventory_delta(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    manifest = seed_zero_action_journal(manifest_path, actual_zero_receipt())
    authorization = authorize_same_batch_addition(manifest, tmp_path, include_completed=False)
    authorization_before = authorization.read_bytes()
    provider_port = FakeProvider([row(
        "K5u-HM2d90U",
        status="draft",
        provider_id="K5u-HM2d90U",
        title="shorts-qQluNEfSVHk-ee84767f66d7",
        description="https://youtu.be/qQluNEfSVHk",
        urls=["https://www.youtube.com/watch?v=qQluNEfSVHk"],
    )])
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    assert runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))["status"] == "complete"
    assert provider_port.retry_attach_calls == 1
    assert authorization.read_bytes() == authorization_before
    journal = json.loads(manifest.journal.read_text())
    proof = next(x for x in journal["history"] if x["event"] == "attachment_zero_action_proved")
    assert proof["observed_added_identities"] == ["K5u-HM2d90U"]
    assert proof["observed_missing_identities"] == []


def test_attached_wy_receipt_stays_on_original_recovery_and_never_retries(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    receipt = json.loads((Path(__file__).with_name("fixtures") / "wy-attached-receipt.json").read_text())
    seed_zero_action_journal(manifest_path, receipt)
    provider_port = FakeProvider()
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    with pytest.raises(publisher.AmbiguousProviderState):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.retry_attach_calls == 0


def test_legacy_zero_action_without_authorization_is_denied_even_when_inventory_unchanged(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    seed_zero_action_journal(manifest_path, actual_zero_receipt())
    provider_port = FakeProvider()
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    with pytest.raises(publisher.AmbiguousProviderState, match="authorization is missing"):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.retry_attach_calls == 0


@pytest.mark.parametrize("evidence_name", ["attachment_invocation.json", "attachment_receipt.json"])
def test_legacy_authorization_rejects_same_shape_bytes_changed_after_review(
    tmp_path: Path, evidence_name: str
) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    manifest = seed_zero_action_journal(manifest_path, actual_zero_receipt())
    authorize_same_batch_addition(manifest, tmp_path, include_completed=False)
    evidence = manifest.video.parent / "provider" / evidence_name
    value = json.loads(evidence.read_text())
    evidence.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=4) + "\n")
    provider_port = FakeProvider()
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    with pytest.raises(publisher.AmbiguousProviderState, match="authorization binding is invalid"):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.retry_attach_calls == 0


def authorize_reviewed_external(
    target: publisher.PublishManifest,
    root: Path,
    *,
    provider_id: str = "externVID01",
    title: str = "AI학교 외부 예약",
    status: str = "scheduled",
    mismatch_second_snapshot: bool = False,
) -> None:
    authorization_path = authorize_same_batch_addition(target, root, include_completed=False)
    urls = [
        f"https://studio.youtube.com/video/{provider_id}/edit",
        f"https://studio.youtube.com/video/{provider_id}/comments",
    ]
    row_value = {
        "identity": provider_id,
        "provider_id": provider_id,
        "status": status,
        "title": unicodedata.normalize("NFD", title),
        "description": "",
        "urls": urls,
        "page": 1,
        "privacy": "VIDEO_PRIVACY_PRIVATE",
        "draft_status": "DRAFT_STATUS_NONE",
        "scheduled_raw": {"scheduledPublishings": [{
            "scheduledTimeSeconds": "1788692400",
            "action": "SCHEDULED_PUBLISHING_ACTION_SET_PUBLIC",
            "status": "SCHEDULED_PUBLISHING_STATUS_SCHEDULED",
        }]},
    }
    row_bytes = json.dumps(row_value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    bindings = []
    rows_hash = ""
    for index, scan_id in enumerate(("1" * 32, "2" * 32)):
        rows = [row_value]
        if mismatch_second_snapshot and index == 1:
            rows = [row_value, {"identity": "unrelated", "status": "public"}]
        rows_bytes = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        if index == 0:
            rows_hash = hashlib.sha256(rows_bytes).hexdigest()
        snapshot = root / f"inventory-provider-model-{scan_id}.json"
        snapshot.write_text(json.dumps({
            "status": "pass",
            "scan_id": scan_id,
            "captured_at": f"2026-09-15T06:4{index}:00.000Z",
            "rows": rows,
        }, ensure_ascii=False))
        bindings.append({"path": str(snapshot), "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest()})
    authorization = json.loads(authorization_path.read_text())
    authorization["reviewed_external_rows"] = [{
        "provider_id": provider_id,
        "status": "scheduled",
        "snapshots": bindings,
        "normalized_rows_sha256": rows_hash,
        "row_sha256": hashlib.sha256(row_bytes).hexdigest(),
    }]
    authorization_path.write_text(json.dumps(authorization))


def test_zero_action_retry_accepts_reviewed_external_scheduled_row(tmp_path: Path) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    manifest = seed_zero_action_journal(manifest_path, actual_zero_receipt())
    authorize_reviewed_external(manifest, tmp_path)
    provider_port = FakeProvider([row(
        "externVID01",
        status="scheduled",
        provider_id="externVID01",
        title="AI에 미친자가 저지른 일 | 4일차",
        description="외부 작업 CTA https://aixschool.kr/admission/",
        urls=[
            "https://studio.youtube.com/video/externVID01/edit",
            "https://studio.youtube.com/video/externVID01/comments",
            "https://aixschool.kr/admission/",
        ],
        page=3,
        scheduled_at="2026-09-06T20:00:00+09:00",
    )])
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    assert runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))["status"] == "complete"
    assert provider_port.retry_attach_calls == 1


@pytest.mark.parametrize(
    "case",
    ["target_marker", "target_title", "target_url", "sentinel"],
)
def test_reviewed_external_row_fail_closed(tmp_path: Path, case: str) -> None:
    manifest_path, digest = make_manifest(tmp_path)
    manifest = seed_zero_action_journal(manifest_path, actual_zero_receipt())
    title = {
        "target_marker": manifest.source_key,
        "target_title": manifest.title,
        "sentinel": manifest.draft_sentinel,
    }.get(case, "현재 외부 메타데이터")
    authorize_reviewed_external(
        manifest,
        tmp_path,
        title="AI학교 외부 예약",
        status="draft" if case == "draft" else "scheduled",
        mismatch_second_snapshot=case == "snapshot_drift",
    )
    provider_port = FakeProvider([row(
        "externVID01",
        status="scheduled",
        provider_id="externVID01",
        title=title,
        urls=([
            "https://studio.youtube.com/video/externVID01/edit",
            "https://studio.youtube.com/video/externVID01/comments",
        ] + ([manifest.canonical_urls[0]] if case == "target_url" else [])),
        scheduled_at="2026-09-07T20:00:00+09:00" if case == "date_drift" else "2026-09-06T20:00:00+09:00",
    )])
    runner = make_runner(provider_port, FakeCrm(), tmp_path, digest)
    with pytest.raises(publisher.AmbiguousProviderState):
        runner.run(manifest_path, now=datetime(2026, 9, 5, 9, tzinfo=KST))
    assert provider_port.retry_attach_calls == 0

def test_zero_action_proof_accepts_a_pre_click_failure_and_refuses_a_later_one():
    """A create control that never rendered touched nothing; a chosen file might have."""
    proves = publisher._receipt_proves_zero_attachment_action
    empty = {"dialog_count": 0, "title_count": 0, "upload_text": "", "files": []}
    assert proves({"error": "extension disconnected"})
    assert proves({"attachment_stage": "context_ready",
                   "error": "create-control-cardinality:0", "diagnostic": empty})
    assert proves({"attachment_stage": "before_open", "error": "boom", "diagnostic": empty})
    # Past the upload control a file may already have been selected.
    for stage in ("upload_control_opened", "file_input_ready", "set_input_files_invoked"):
        assert not proves({"attachment_stage": stage, "error": "boom", "diagnostic": empty})
    # A diagnostic that shows any upload state is not proof of zero action.
    for dirty in ({**empty, "dialog_count": 1}, {**empty, "title_count": 1},
                  {**empty, "upload_text": "업로드 중"}, {**empty, "files": ["final.mp4"]}):
        assert not proves({"attachment_stage": "context_ready", "error": "boom", "diagnostic": dirty})
    # No diagnostic at all proves nothing.
    assert not proves({"attachment_stage": "context_ready", "error": "boom"})

def test_slot_reserved_on_an_earlier_day_is_replannable_but_a_same_day_one_is_not():
    """Yesterday's uncommitted reservation may move; today's still stops for a human."""
    reserved_at = publisher._slot_reserved_at
    yesterday = {"history": [
        {"event": "attachment_reserved", "at": "2026-09-15T16:25:05+09:00"},
        {"event": "manual_remediation_required", "at": "2026-09-16T12:13:54+09:00"},
    ]}
    assert reserved_at(yesterday).date().isoformat() == "2026-09-15"
    # The most recent slot-setting event wins, not the first one.
    replanned = {"history": [
        {"event": "attachment_reserved", "at": "2026-09-15T16:25:05+09:00"},
        {"event": "slot_replanned_before_schedule", "at": "2026-09-16T09:00:00+09:00"},
    ]}
    assert reserved_at(replanned).date().isoformat() == "2026-09-16"
    # Nothing to go on means no extra replan reason, so the guard still holds.
    assert reserved_at({}) is None
    assert reserved_at({"history": [{"event": "attachment_invocation_interrupted",
                                     "at": "2026-09-16T09:00:00+09:00"}]}) is None
    assert reserved_at({"history": [{"event": "attachment_reserved", "at": "not-a-time"}]}) is None


def test_same_day_slot_conflict_replans_only_when_no_row_of_this_candidate_is_scheduled():
    """다른 영상이 자리를 가졌으면 옮기고, 이 후보나 그 복제본이 예약돼 있으면 사람에게 넘긴다."""
    from types import SimpleNamespace as NS
    holds = publisher._candidate_holds_a_scheduled_row

    class Inv:
        def __init__(self, rows, dupes=()):
            self.rows = rows
            self._dupes = dupes
        def matches(self, manifest):
            return tuple(self._dupes)

    attached = "ZgIor8ELtJY"
    other = NS(identity="yfuMe0JPryI", status="scheduled")
    mine_draft = NS(identity=attached, status="draft")
    # 앞 후보가 멈춘 사이 다른 영상이 그 자리를 채웠다: 옮겨도 된다.
    assert not holds(Inv([other, mine_draft]), None, attached)
    # 이 후보의 첨부 초안이 이미 예약돼 있다: 사람이 봐야 한다.
    assert holds(Inv([NS(identity=attached, status="scheduled")]), None, attached)
    # 이 후보의 복제본(같은 매니페스트와 맞는 다른 행)이 예약돼 있다: 사람이 봐야 한다.
    dupe = NS(identity="DupDupDup11", status="scheduled")
    assert holds(Inv([other, dupe], dupes=[dupe]), None, attached)
    # 복제본이 있어도 예약 전이면 이 후보가 자리를 가진 게 아니다.
    dupe_draft = NS(identity="DupDupDup11", status="private")
    assert not holds(Inv([other, dupe_draft], dupes=[dupe_draft]), None, attached)
