from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

import youtube_shorts_aside_adapter as adapter
import youtube_shorts_publisher as publisher


KST = ZoneInfo("Asia/Seoul")
SOURCE = "abcDEF12345"
PROVIDER_ID = "newVID00001"
NOW = datetime(2026, 9, 5, 9, 0, tzinfo=KST)
CURRENT_UI_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "youtube_shorts_adapter_current_ui.json").read_text(
        encoding="utf-8"
    )
)
def make_manifest(root: Path) -> tuple[Path, publisher.PublishManifest]:
    video = root / "shorts" / "final.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"sealed-final-video")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    short_url = f"https://youtu.be/{SOURCE}"
    watch_url = f"https://www.youtube.com/watch?v={SOURCE}"
    manifest_path = root / "provider-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "source_key": SOURCE,
                "title": "정확한 쇼츠 제목",
                "description": f"원본 영상: {short_url}\n정본 링크: {watch_url}",
                "final_mp4": str(video),
                "final_mp4_sha256": digest,
                "original_urls": [short_url, watch_url],
                "expected_channel": adapter.CHANNEL_NAME,
                "journal": str(root / "provider" / "journal.json"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path, publisher.load_manifest(manifest_path)


def raw_row(
    identity: str,
    status: str,
    *,
    page: int,
    title: str = "unrelated",
    description: str = "",
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "identity": identity,
        "provider_id": identity,
        "status": status,
        "title": title,
        "description": description,
        "urls": [],
        "page": page,
        "direct_metadata_inspected": True,
        "visibility_control_present": status in {"draft", "private", "scheduled"},
        "no_kids": True,
    }
    if status == "scheduled":
        value["scheduled_at"] = "2026-09-06T11:00:00+09:00"
        value["timezone_evidence"] = CURRENT_UI_FIXTURE["scheduled_direct"][
            "timezone_button_text"
        ]
    if status == "public":
        value["published_at"] = CURRENT_UI_FIXTURE["public_row"]["date_text"]
    return value


def inventory_fixture() -> dict[str, Any]:
    rows = [
        raw_row("public00001", "public", page=1),
        raw_row("sched000001", "scheduled", page=1),
        raw_row("privat00001", "private", page=2),
        raw_row("draft000001", "draft", page=2),
    ]
    rows[0]["urls"] = [
        "https://studio.youtube.com/video/public00001/edit"
    ]
    return {
        "status": "pass",
        "account": "u0",
        "headless": True,
        "channel": adapter.CHANNEL_NAME,
        "pagination_complete": True,
        "terminal_reason": "next_disabled",
        "pages_scanned": 2,
        "pages": [
            {
                "page": 1,
                "row_count": 2,
                "state_counts": {"public": 1, "scheduled": 1, "private": 0, "draft": 0},
                "next_disabled": False,
            },
            {
                "page": 2,
                "row_count": 2,
                "state_counts": {"public": 0, "scheduled": 0, "private": 1, "draft": 1},
                "next_disabled": True,
            },
        ],
        "scanned_states": ["public", "scheduled", "private", "draft"],
        "status_counts": {"public": 1, "scheduled": 1, "private": 1, "draft": 1},
        "rows": rows,
    }


def chunked_inventory_response(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return exact seed or direct-chunk evidence for the static UI rows."""

    base = inventory_fixture()
    direct_rows = base.pop("rows")
    seeds = []
    for row in direct_rows:
        seeds.append(
            {
                "identity": row["identity"],
                "provider_id": row["provider_id"],
                "status": row["status"],
                "title": row["title"],
                "listText": row["title"],
                "urls": list(row["urls"]),
                "page": row["page"],
                "publishedRaw": row.get("published_at", ""),
            }
        )
    common = {
        "status": "pass",
        "account": "u0",
        "headless": True,
        "channel": adapter.CHANNEL_NAME,
        "scan_token": payload["scan_token"],
        "chunk_nonce": payload["chunk_nonce"],
        "captured_at": NOW.isoformat(),
    }
    if "seed_phase" in payload:
        return {
            **base,
            **common,
            "inventory_mode": "seed",
            "seed_phase": payload["seed_phase"],
            "seed_rows": seeds,
        }
    start = int(payload["chunk_start"])
    end = int(payload["chunk_end"])
    by_identity = {row["identity"]: row for row in direct_rows}
    rows = []
    for seed in payload["expected_rows"]:
        row = dict(by_identity[seed["identity"]])
        seed_urls = list(seed["urls"])
        row.update(
            list_title=seed["title"],
            publishedRaw=seed["publishedRaw"],
            seed_urls=seed_urls,
            urls=list(dict.fromkeys([*seed_urls, *row["urls"]])),
            published_at=(
                seed["publishedRaw"] if seed["status"] == "public" else ""
            ),
        )
        rows.append(row)
    return {
        **common,
        "inventory_mode": "direct",
        "chunk_index": payload["chunk_index"],
        "chunk_total": payload["chunk_total"],
        "chunk_start": start,
        "chunk_end": end,
        "expected_identities": list(payload["expected_identities"]),
        "rows": rows,
    }


class CapturingAsideRunner:
    def __init__(self, result: Any):
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        body: str,
        payload: Mapping[str, Any],
        *,
        cwd: Path,
        timeout: int,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {"body": body, "payload": dict(payload), "cwd": cwd, "timeout": timeout}
        )
        return self.result(payload) if callable(self.result) else self.result


def test_inventory_exhaustively_preserves_pagination_and_four_state_evidence(
    tmp_path: Path,
) -> None:
    _path, manifest = make_manifest(tmp_path)
    aside = CapturingAsideRunner(chunked_inventory_response)
    port = adapter.AsideHeadlessU0Provider(runner=aside, clock=lambda: NOW)

    evidence = port.scan_inventory(manifest, phase="attachment_precommit_requery")

    assert evidence["scan_id"]
    assert evidence["phase"] == "attachment_precommit_requery"
    assert evidence["captured_at"] == NOW.isoformat()
    assert evidence["account"] == "u0"
    assert evidence["headless"] is True
    assert evidence["channel"] == adapter.CHANNEL_NAME
    assert evidence["pages_scanned"] == 2
    assert evidence["pages"][-1]["next_disabled"] is True
    assert evidence["status_counts"] == {
        "public": 1,
        "scheduled": 1,
        "private": 1,
        "draft": 1,
    }
    assert {row["status"] for row in evidence["rows"]} == publisher.STATES
    assert all(row["direct_metadata_inspected"] is True for row in evidence["rows"])
    assert evidence["rows"][0]["published_date"] == "2026-09-04"
    assert evidence["rows"][1]["scheduled_at"] == "2026-09-06T11:00:00+09:00"
    assert len(aside.calls) == 3
    initial_seed = aside.calls[0]["payload"]
    assert initial_seed["list_url"] == adapter.STUDIO_SHORTS_URL
    assert initial_seed["channel"] == adapter.CHANNEL_NAME
    assert initial_seed["seed_phase"] == "initial"
    chunk_payload = aside.calls[1]["payload"]
    assert chunk_payload["scan_token"] == initial_seed["scan_token"]
    assert chunk_payload["sentinel"] == manifest.draft_sentinel
    assert chunk_payload["title"] == manifest.title
    assert chunk_payload["chunk_start"] == 0
    assert chunk_payload["chunk_end"] == 4
    assert chunk_payload["expected_identities"] == [
        "public00001",
        "sched000001",
        "privat00001",
        "draft000001",
    ]
    assert chunk_payload["concurrency"] == adapter.INVENTORY_DIRECT_CONCURRENCY
    assert aside.calls[2]["payload"]["seed_phase"] == "final"
    assert aside.calls[2]["payload"]["scan_token"] == initial_seed["scan_token"]
    assert all(call["timeout"] == 100 for call in aside.calls)
    assert "#navigate-after" in aside.calls[0]["body"]
    assert "#navigate-after" not in aside.calls[1]["body"]
    assert "strict_inventory" not in aside.calls[1]["body"]


def test_recorded_logged_in_signin_text_does_not_drive_the_login_gate() -> None:
    recorded = CURRENT_UI_FIXTURE["logged_in_edit_context"]
    assert recorded["page_looks_logged_out_raw"] is True
    assert recorded["explicit_signin_redirect"] is False
    assert recorded["expected_channel_present"] is True
    assert recorded["avatar_button_count"] == 1
    assert recorded["title_control_count"] == 1
    assert recorded["description_control_count"] == 1
    for body in (
        adapter.INVENTORY_SEED_JS,
        adapter.INVENTORY_DIRECT_JS,
        adapter.ATTACH_JS,
        adapter.SCHEDULE_JS,
        adapter.DIRECT_JS,
    ):
        assert "pageLooksLoggedOut" not in body
        assert "signinRedirect" in body
        assert "avatarCount" in body or "avatars.length" in body


def test_recorded_generic_timezone_label_preserves_exact_kst_date_and_time() -> None:
    recorded = CURRENT_UI_FIXTURE["scheduled_direct"]
    assert adapter._timezone_contract_present(recorded["timezone_button_text"])
    assert adapter._exact_kst_iso(
        None,
        date_text=recorded["date_text"],
        time_text=recorded["time_text"],
    ) == "2026-09-08T20:00:00+09:00"
    assert adapter._exact_kst_date(
        CURRENT_UI_FIXTURE["public_row"]["date_text"]
    ) == "2026-09-04"


def test_provider_js_checks_raw_selector_cardinality_before_element_selection() -> None:
    row_contract = CURRENT_UI_FIXTURE["shorts_list_row"]
    for body in (
        adapter.INVENTORY_SEED_JS,
        adapter.INVENTORY_DIRECT_JS,
        adapter.ATTACH_JS,
        adapter.SCHEDULE_JS,
        adapter.DIRECT_JS,
    ):
        assert ".first()" not in body
        assert ".last()" not in body
        assert "cardinality" in body
    assert "upload_flow_preserved:true" in adapter.ATTACH_JS
    assert "exact_draft_recovery=" in adapter.SCHEDULE_JS
    assert "getByText('초안 수정'" in adapter.SCHEDULE_JS
    assert adapter.SCHEDULE_JS.index("no-kids-not-selected") < adapter.SCHEDULE_JS.index("await badge.click()")
    assert "payload.prepare_only" in adapter.SCHEDULE_JS
    assert "dialogSet.locator('#title-textarea #textbox').isVisible()" in adapter.ATTACH_JS
    assert "row-title-not-visible" in adapter.INVENTORY_SEED_JS
    assert "row-date-cardinality" in adapter.INVENTORY_SEED_JS
    assert "row-date-not-visible" in adapter.INVENTORY_SEED_JS
    assert row_contract["rows_inspected"] == 30
    assert row_contract["old_date_count_values"] == [0]
    assert row_contract["date_count_values"] == [1]
    assert row_contract["date_visible_count_values"] == [1]
    assert row_contract["status_count_values"] == [1]
    assert row_contract["status_visible_count_values"] == [1]
    assert f"row.locator('{row_contract['date_selector']}')" in adapter.INVENTORY_SEED_JS
    assert (
        f"row.locator('{row_contract['old_date_selector']}')"
        not in adapter.INVENTORY_SEED_JS
    )
    assert "titles.count()!==1||!await titles.isVisible()" in adapter.INVENTORY_DIRECT_JS
    assert "exact(root,'#title-textarea #textbox','metadata-title')" in adapter.SCHEDULE_JS


@pytest.mark.parametrize(
    "name,body",
    [
        pytest.param("inventory_seed", adapter.INVENTORY_SEED_JS, id="inventory-seed"),
        pytest.param(
            "inventory_direct", adapter.INVENTORY_DIRECT_JS, id="inventory-direct"
        ),
        pytest.param("attach", adapter.ATTACH_JS, id="attach"),
        pytest.param("schedule", adapter.SCHEDULE_JS, id="schedule"),
        pytest.param("direct", adapter.DIRECT_JS, id="direct"),
    ],
)
def test_every_actual_composed_provider_program_passes_node_syntax_check(
    name: str,
    body: str,
) -> None:
    from aside_browser import JS_COMMON, _payload_expression

    composed = adapter._compose_provider_javascript(
        JS_COMMON,
        _payload_expression({"syntax_probe": name}),
        body,
    )
    wrapped_as_run_repl = f"(async()=>{{\n{composed}\n}})();\n"
    completed = subprocess.run(
        ["node", "--check", "-"],
        input=wrapped_as_run_repl,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_composed_provider_programs_remain_isolated_in_a_persistent_scope() -> None:
    from aside_browser import JS_COMMON, _payload_expression

    programs = [
        adapter._compose_provider_javascript(
            JS_COMMON,
            _payload_expression({"syntax_probe": index}),
            body,
        )
        for index, body in enumerate(
            (
                adapter.INVENTORY_SEED_JS,
                adapter.INVENTORY_DIRECT_JS,
                adapter.ATTACH_JS,
                adapter.SCHEDULE_JS,
                adapter.DIRECT_JS,
            ),
            1,
        )
    ]
    persistent_script = "\n".join(
        f"(async()=>{{\n{program}\n}})();" for program in programs
    )
    completed = subprocess.run(
        ["node", "--check", "-"],
        input=persistent_script,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(pagination_complete=False),
        lambda value: value["pages"][-1].update(next_disabled=False),
        lambda value: value.update(account="u1"),
        lambda value: value.update(chunk_nonce="stale-replayed-nonce"),
        lambda value: value["seed_rows"][0].update(status="other"),
    ],
)
def test_inventory_fails_closed_on_incomplete_or_unbound_evidence(
    mutation: Any, tmp_path: Path
) -> None:
    _path, manifest = make_manifest(tmp_path)

    def result(payload: Mapping[str, Any]) -> dict[str, Any]:
        raw = chunked_inventory_response(payload)
        if "seed_phase" in payload:
            mutation(raw)
        return raw

    port = adapter.AsideHeadlessU0Provider(
        runner=CapturingAsideRunner(result), clock=lambda: NOW
    )
    with pytest.raises(publisher.PublisherSafetyError):
        port.scan_inventory(manifest, phase="initial_recovery_or_duplicate_scan")


def test_inventory_merges_multiple_direct_metadata_chunks_under_one_scan_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(adapter, "INVENTORY_DIRECT_CHUNK_SIZE", 2)
    _path, manifest = make_manifest(tmp_path)
    aside = CapturingAsideRunner(chunked_inventory_response)
    port = adapter.AsideHeadlessU0Provider(runner=aside, clock=lambda: NOW)

    evidence = port.scan_inventory(manifest, phase="initial_recovery_or_duplicate_scan")

    assert len(evidence["rows"]) == 4
    assert len(aside.calls) == 4  # initial seed, two direct chunks, final seed
    assert {call["payload"]["scan_token"] for call in aside.calls} == {
        evidence["scan_id"]
    }
    assert len({call["payload"]["chunk_nonce"] for call in aside.calls}) == 4
    direct_calls = [call for call in aside.calls if "chunk_start" in call["payload"]]
    assert [call["payload"]["expected_identities"] for call in direct_calls] == [
        ["public00001", "sched000001"],
        ["privat00001", "draft000001"],
    ]


@pytest.mark.parametrize("row_count", [174, 175])
def test_measured_174_plus_row_plan_covers_every_row_without_repeating_pagination(
    row_count: int,
    tmp_path: Path,
) -> None:
    _path, manifest = make_manifest(tmp_path)
    states = ("public", "scheduled", "private", "draft")
    seeds = [
        {
            "identity": f"v{index:010d}",
            "provider_id": f"v{index:010d}",
            "status": states[index % len(states)],
            "title": f"title-{index}",
            "listText": f"title-{index}",
            "urls": [],
            "page": index // 30 + 1,
            "publishedRaw": "2026-09-04" if index % len(states) == 0 else "",
        }
        for index in range(row_count)
    ]
    pages = []
    for page_number in range(1, 7):
        page_rows = [row for row in seeds if row["page"] == page_number]
        pages.append(
            {
                "page": page_number,
                "row_count": len(page_rows),
                "state_counts": {
                    state: sum(row["status"] == state for row in page_rows)
                    for state in states
                },
                "next_disabled": page_number == 6,
            }
        )

    def result(payload: Mapping[str, Any]) -> dict[str, Any]:
        common = {
            "status": "pass",
            "account": "u0",
            "headless": True,
            "channel": adapter.CHANNEL_NAME,
            "scan_token": payload["scan_token"],
            "chunk_nonce": payload["chunk_nonce"],
            "captured_at": NOW.isoformat(),
        }
        if "seed_phase" in payload:
            return {
                **common,
                "inventory_mode": "seed",
                "seed_phase": payload["seed_phase"],
                "pagination_complete": True,
                "terminal_reason": "next_disabled",
                "pages_scanned": len(pages),
                "pages": pages,
                "scanned_states": list(states),
                "status_counts": {
                    state: sum(row["status"] == state for row in seeds)
                    for state in states
                },
                "seed_rows": seeds,
            }
        direct_rows = []
        for seed in payload["expected_rows"]:
            seed_urls = list(seed["urls"])
            row = {
                **seed,
                "list_title": seed["title"],
                "seed_urls": seed_urls,
                "description": "",
                "urls": seed_urls,
                "direct_metadata_inspected": True,
                "visibility_control_present": seed["status"]
                in {"scheduled", "private", "draft"},
                "no_kids": True,
                "published_at": (
                    seed["publishedRaw"] if seed["status"] == "public" else ""
                ),
            }
            if seed["status"] == "scheduled":
                row["scheduled_at"] = "2026-09-06T11:00:00+09:00"
                row["timezone_evidence"] = "시간대"
            direct_rows.append(row)
        return {
            **common,
            "inventory_mode": "direct",
            "chunk_index": payload["chunk_index"],
            "chunk_total": payload["chunk_total"],
            "chunk_start": payload["chunk_start"],
            "chunk_end": payload["chunk_end"],
            "expected_identities": list(payload["expected_identities"]),
            "rows": direct_rows,
        }

    aside = CapturingAsideRunner(result)
    port = adapter.AsideHeadlessU0Provider(runner=aside, clock=lambda: NOW)

    evidence = port.scan_inventory(manifest, phase="initial_recovery_or_duplicate_scan")

    direct_calls = [call for call in aside.calls if "chunk_start" in call["payload"]]
    assert len(evidence["rows"]) == row_count
    assert [row["identity"] for row in evidence["rows"]] == [
        row["identity"] for row in seeds
    ]
    assert len(aside.calls) == 2 + (row_count + adapter.INVENTORY_DIRECT_CHUNK_SIZE - 1) // adapter.INVENTORY_DIRECT_CHUNK_SIZE
    assert len(direct_calls) == math.ceil(
        row_count / adapter.INVENTORY_DIRECT_CHUNK_SIZE
    )
    assert [
        call["payload"].get("seed_phase")
        for call in aside.calls
        if "seed_phase" in call["payload"]
    ] == ["initial", "final"]
    assert all("#navigate-after" not in call["body"] for call in direct_calls)
    assert all("Promise.allSettled" in call["body"] for call in direct_calls)
    assert all(
        call["payload"]["concurrency"] == adapter.INVENTORY_DIRECT_CONCURRENCY
        for call in direct_calls
    )
    # This is a hermetic coverage/cardinality test. Live timing observations
    # are operational evidence, not a unit-test dependency.


def _swap_private_and_draft_states(raw: dict[str, Any]) -> None:
    by_identity = {row["identity"]: row for row in raw["rows"]}
    by_identity["privat00001"]["status"] = "draft"
    by_identity["draft000001"]["status"] = "private"


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(_swap_private_and_draft_states, id="private-draft-state-swap"),
        pytest.param(lambda raw: raw["rows"][0].update(page=99), id="page"),
        pytest.param(
            lambda raw: raw["rows"][0].update(list_title="drifted-list-title"),
            id="list-title",
        ),
        pytest.param(
            lambda raw: raw["rows"][0].update(title="drifted-direct-title"),
            id="metadata-title",
        ),
        pytest.param(
            lambda raw: raw["rows"][0].update(provider_id="other000001"),
            id="provider-id",
        ),
        pytest.param(
            lambda raw: raw["rows"][0].update(seed_urls=["https://invalid.example/"]),
            id="seed-url-binding",
        ),
        pytest.param(lambda raw: raw["rows"][0].update(urls=[]), id="seed-url-prefix"),
        pytest.param(
            lambda raw: raw["rows"][0]["urls"].append("https://invalid.example/"),
            id="unexpected-url",
        ),
        pytest.param(
            lambda raw: raw["rows"][0].update(publishedRaw="2026-09-03"),
            id="published-raw",
        ),
        pytest.param(
            lambda raw: raw["rows"][0].update(published_at="2026-09-03"),
            id="published-at",
        ),
    ],
)
def test_inventory_rejects_direct_row_drift_from_exact_seed_binding(
    mutation: Any,
    tmp_path: Path,
) -> None:
    _path, manifest = make_manifest(tmp_path)

    def result(payload: Mapping[str, Any]) -> dict[str, Any]:
        raw = chunked_inventory_response(payload)
        if "chunk_start" in payload:
            mutation(raw)
        return raw

    port = adapter.AsideHeadlessU0Provider(
        runner=CapturingAsideRunner(result), clock=lambda: NOW
    )
    with pytest.raises(
        publisher.PublisherSafetyError,
        match="direct (row|metadata|URLs|published)",
    ):
        port.scan_inventory(manifest, phase="initial_recovery_or_duplicate_scan")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value, payload: value.update(
            captured_at="2026-09-05T08:50:00+09:00"
        ),
        lambda value, payload: (
            value["rows"].pop()
            if "chunk_start" in payload and value["rows"]
            else None
        ),
        lambda value, payload: (
            value["rows"].append(dict(value["rows"][0]))
            if "chunk_start" in payload and value["rows"]
            else None
        ),
        lambda value, payload: (
            value["rows"].reverse()
            if "chunk_start" in payload and len(value["rows"]) > 1
            else None
        ),
        lambda value, payload: (
            value.update(chunk_nonce="stale-direct-nonce")
            if "chunk_start" in payload
            else None
        ),
        lambda value, payload: (
            value["seed_rows"][1].update(
                identity=value["seed_rows"][0]["identity"]
            )
            if "seed_phase" in payload
            else None
        ),
        lambda value, payload: (
            value["seed_rows"][0].update(title="changed-during-chunks")
            if payload.get("seed_phase") == "final"
            else None
        ),
        lambda value, payload: (
            value["seed_rows"].reverse()
            if payload.get("seed_phase") == "final"
            else None
        ),
        lambda value, payload: (
            value["seed_rows"][0]["urls"].append("https://drift.invalid/")
            if payload.get("seed_phase") == "final"
            else None
        ),
    ],
)
def test_inventory_chunks_fail_closed_on_stale_missing_duplicate_or_drifted_evidence(
    mutation: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(adapter, "INVENTORY_DIRECT_CHUNK_SIZE", 2)
    _path, manifest = make_manifest(tmp_path)

    def result(payload: Mapping[str, Any]) -> dict[str, Any]:
        raw = chunked_inventory_response(payload)
        mutation(raw, payload)
        return raw

    port = adapter.AsideHeadlessU0Provider(
        runner=CapturingAsideRunner(result), clock=lambda: NOW
    )
    with pytest.raises(publisher.PublisherSafetyError):
        port.scan_inventory(manifest, phase="initial_recovery_or_duplicate_scan")


@pytest.mark.parametrize("final_offset_minutes", [5, 6])
def test_inventory_chunks_fail_closed_at_or_beyond_freshness_window(
    final_offset_minutes: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(adapter, "INVENTORY_DIRECT_CHUNK_SIZE", 2)
    _path, manifest = make_manifest(tmp_path)
    observed_now = [NOW]

    def result(payload: Mapping[str, Any]) -> dict[str, Any]:
        if payload.get("seed_phase") == "initial":
            captured = NOW
        elif payload.get("seed_phase") == "final":
            captured = NOW + timedelta(minutes=final_offset_minutes)
        elif payload["chunk_start"] == 0:
            captured = NOW + timedelta(minutes=3)
        else:
            captured = NOW + timedelta(minutes=4)
        observed_now[0] = captured
        raw = chunked_inventory_response(payload)
        raw["captured_at"] = captured.isoformat()
        return raw

    port = adapter.AsideHeadlessU0Provider(
        runner=CapturingAsideRunner(result), clock=lambda: observed_now[0]
    )
    with pytest.raises(
        publisher.PublisherSafetyError,
        match="logical scan exceeded its freshness window",
    ):
        port.scan_inventory(manifest, phase="initial_recovery_or_duplicate_scan")


def test_inventory_fails_closed_on_nonpositive_chunk_size(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(adapter, "INVENTORY_DIRECT_CHUNK_SIZE", 0)
    _path, manifest = make_manifest(tmp_path)
    port = adapter.AsideHeadlessU0Provider(
        runner=CapturingAsideRunner(chunked_inventory_response), clock=lambda: NOW
    )
    with pytest.raises(
        publisher.PublisherSafetyError,
        match="chunk size must be positive",
    ):
        port.scan_inventory(manifest, phase="initial_recovery_or_duplicate_scan")


def test_inventory_discards_partial_chunks_when_a_later_aside_call_disconnects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(adapter, "INVENTORY_DIRECT_CHUNK_SIZE", 2)
    _path, manifest = make_manifest(tmp_path)

    def result(payload: Mapping[str, Any]) -> dict[str, Any]:
        if payload.get("chunk_start", -1) >= 2:
            raise TimeoutError("simulated Aside daemon disconnect")
        return chunked_inventory_response(payload)

    aside = CapturingAsideRunner(result)
    port = adapter.AsideHeadlessU0Provider(runner=aside, clock=lambda: NOW)
    with pytest.raises(adapter.LiveDependencyError, match="Aside CLI headless u0 call failed"):
        port.scan_inventory(manifest, phase="initial_recovery_or_duplicate_scan")
    assert len(aside.calls) == 3


def test_attach_sends_exact_final_mp4_once_with_sentinel_and_observed_receipt(
    tmp_path: Path,
) -> None:
    _path, manifest = make_manifest(tmp_path)
    def receipt(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "status": "attached",
            "account": "u0",
            "headless": True,
            "channel": adapter.CHANNEL_NAME,
            "provider_observed_attachment_click_count": 1,
            "attachment_name": "final.mp4",
            "attachment_sha256": manifest.video_sha256,
            "draft_sentinel": manifest.draft_sentinel,
            "provider_id": PROVIDER_ID,
            "displayed_filename": True,
            "upload_flow_preserved": True,
            "session_marker": payload["session_marker"],
        }

    aside = CapturingAsideRunner(receipt)
    port = adapter.AsideHeadlessU0Provider(runner=aside, clock=lambda: NOW)

    result = port.attach_once(manifest, draft_sentinel=manifest.draft_sentinel)

    assert result["provider_observed_attachment_click_count"] == 1
    assert len(aside.calls) == 1
    payload = aside.calls[0]["payload"]
    assert payload["file"]["name"] == "final.mp4"
    assert payload["sha256"] == manifest.video_sha256
    assert payload["sentinel"] == manifest.draft_sentinel
    assert payload["session_marker"]
    assert payload["file"]["base64"]
    assert "attachActions++" in aside.calls[0]["body"]
    assert "displayed_filename" in aside.calls[0]["body"]


def test_attach_rejects_missing_provider_observation(tmp_path: Path) -> None:
    _path, manifest = make_manifest(tmp_path)
    aside = CapturingAsideRunner(
        {
            "status": "attached",
            "account": "u0",
            "headless": True,
            "channel": adapter.CHANNEL_NAME,
            "provider_observed_attachment_click_count": 0,
        }
    )
    port = adapter.AsideHeadlessU0Provider(runner=aside, clock=lambda: NOW)
    with pytest.raises(publisher.AmbiguousProviderState, match="attachment receipt"):
        port.attach_once(manifest, draft_sentinel=manifest.draft_sentinel)
    assert len(aside.calls) == 1


def make_provider_row(manifest: publisher.PublishManifest) -> publisher.ProviderRow:
    return publisher.ProviderRow(
        identity=PROVIDER_ID,
        provider_id=PROVIDER_ID,
        status="draft",
        title=manifest.draft_sentinel,
        description="",
        urls=(),
        page=1,
        direct_metadata_inspected=True,
        scheduled_at=None,
        published_at=None,
        visibility_control_present=True,
    )


def test_schedule_recovers_exact_draft_and_exposes_one_click_receipt(tmp_path: Path) -> None:
    _path, manifest = make_manifest(tmp_path)
    slot = datetime(2026, 9, 6, 11, 0, tzinfo=KST)
    receipt = {
        "status": "committed",
        "account": "u0",
        "headless": True,
        "channel": adapter.CHANNEL_NAME,
        "provider_id": PROVIDER_ID,
        "provider_observed_schedule_click_count": 1,
        "title": manifest.title,
        "description": manifest.description,
        "no_kids": True,
        "scheduled_at": slot.isoformat(),
        "timezone": "Asia/Seoul",
        "flow": "upload",
    }
    aside = CapturingAsideRunner(receipt)
    port = adapter.AsideHeadlessU0Provider(runner=aside, clock=lambda: NOW)

    result = port.schedule_once(manifest, make_provider_row(manifest), slot)

    assert result["provider_observed_schedule_click_count"] == 1
    assert len(aside.calls) == 1
    payload = aside.calls[0]["payload"]
    assert payload["identity"] == payload["provider_id"] == PROVIDER_ID
    assert payload["title"] == manifest.title
    assert payload["description"] == manifest.description
    assert set(payload["urls"]) == set(manifest.canonical_urls)
    assert payload["slot"] == slot.isoformat()
    assert payload["provider_date"] == "2026. 9. 6."
    assert payload["provider_time"] == "오전 11:00"
    assert "scheduleClicks=1;await done.click()" in aside.calls[0]["body"]


def test_schedule_refuses_a_providerless_or_nonexact_draft_without_aside_access(
    tmp_path: Path,
) -> None:
    _path, manifest = make_manifest(tmp_path)
    row = make_provider_row(manifest)
    bad = publisher.ProviderRow(
        **{**row.__dict__, "identity": "draft-signature", "provider_id": ""}
    )
    aside = CapturingAsideRunner({})
    port = adapter.AsideHeadlessU0Provider(runner=aside, clock=lambda: NOW)
    with pytest.raises(publisher.ScheduleControlUnavailable):
        port.schedule_once(manifest, bad, datetime(2026, 9, 6, 11, tzinfo=KST))
    assert aside.calls == []


def test_direct_requery_returns_exact_provider_metadata_and_kst_time(tmp_path: Path) -> None:
    _path, manifest = make_manifest(tmp_path)
    raw = {
        "status": "pass",
        "account": "u0",
        "headless": True,
        "channel": adapter.CHANNEL_NAME,
        "provider_id": PROVIDER_ID,
        "shorts_url": f"https://www.youtube.com/shorts/{PROVIDER_ID}",
        "exact_row_count": 1,
        "title": manifest.title,
        "description": manifest.description,
        "original_urls": list(manifest.canonical_urls),
        "no_kids": True,
        "provider_status": "scheduled",
        "schedule_date": "2026. 9. 6.",
        "schedule_time": "오전 11:00",
        "timezone_evidence": "서울(GMT+09:00)",
    }
    aside = CapturingAsideRunner(raw)
    port = adapter.AsideHeadlessU0Provider(runner=aside, clock=lambda: NOW)

    evidence = port.direct_requery(manifest, PROVIDER_ID)
    slot = datetime(2026, 9, 6, 11, tzinfo=KST)
    verified = publisher.validate_direct_evidence(
        evidence, manifest, PROVIDER_ID, slot, now=NOW
    )

    assert verified["provider_id"] == PROVIDER_ID
    assert verified["shorts_url"] == f"https://www.youtube.com/shorts/{PROVIDER_ID}"
    assert all(verified["checks"].values())
    assert evidence["scheduled_at"] == slot.isoformat()
    assert evidence["status"] == "scheduled"
    assert len(aside.calls) == 1


def create_crm_db(path: Path, *, dedupe_key: str | None = None) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE events (dedupe_key TEXT, status TEXT, channel TEXT, "
            "campaign TEXT, stage TEXT, metric_count INTEGER)"
        )
        if dedupe_key:
            connection.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?)",
                (
                    dedupe_key,
                    "success",
                    "youtube_shorts",
                    "youtube-content-repurpose",
                    "sent",
                    1,
                ),
            )


def make_tracker(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    os.chmod(path, 0o700)
    return path


def test_crm_port_lookup_and_emit_are_deduped_and_payload_minimal(tmp_path: Path) -> None:
    db = tmp_path / "events.sqlite3"
    create_crm_db(db, dedupe_key="external-existing")
    tracker = make_tracker(tmp_path / "aimax-crm-track")
    calls: list[list[str]] = []

    def command_runner(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0, stdout='{"ok":true,"created":true,"event_id":"event-1"}', stderr=""
        )

    port = adapter.AimaxCrmTrackPort(
        tracker=tracker, db_path=db, command_runner=command_runner
    )
    assert port.lookup("external-existing") == {
        "dedupe_key": "external-existing",
        "status": "success",
        "channel": "youtube_shorts",
        "campaign": "youtube-content-repurpose",
        "stage": "sent",
        "metric_count": 1,
    }
    payload = {
        "channel": "youtube_shorts",
        "campaign": "youtube-content-repurpose",
        "stage": "sent",
        "status": "success",
        "count": 1,
        "dedupe_key": "external-new",
    }
    receipt = port.emit(payload)
    assert receipt == {
        "status": "success",
        "dedupe_key": "external-new",
        "channel": "youtube_shorts",
        "campaign": "youtube-content-repurpose",
        "stage": "sent",
        "count": 1,
        "created": True,
        "event_id": "event-1",
    }
    assert len(calls) == 1
    command = calls[0]
    assert command[:2] == [str(tracker), "emit"]
    assert "--dedupe-key" in command
    assert "--content" not in command
    assert not any("http" in part or "@" in part for part in command)


@pytest.mark.parametrize("extra", [{"body": "secret"}, {"url": "https://example.com"}])
def test_crm_port_rejects_body_url_or_pii_fields_before_command(
    extra: dict[str, Any], tmp_path: Path
) -> None:
    db = tmp_path / "events.sqlite3"
    create_crm_db(db)
    tracker = make_tracker(tmp_path / "aimax-crm-track")
    command_runner = mock.Mock()
    port = adapter.AimaxCrmTrackPort(
        tracker=tracker, db_path=db, command_runner=command_runner
    )
    payload = {
        "channel": "youtube_shorts",
        "campaign": "youtube-content-repurpose",
        "stage": "sent",
        "status": "success",
        "count": 1,
        "dedupe_key": "external-new",
        **extra,
    }
    with pytest.raises(publisher.PublisherSafetyError, match="only"):
        port.emit(payload)
    command_runner.assert_not_called()


def test_publisher_reconciles_existing_crm_dedupe_without_provider_or_emit(
    tmp_path: Path,
) -> None:
    manifest_path, manifest = make_manifest(tmp_path)
    dedupe = publisher._crm_dedupe_key(manifest)
    db = tmp_path / "events.sqlite3"
    create_crm_db(db, dedupe_key=dedupe)
    tracker = make_tracker(tmp_path / "aimax-crm-track")
    command_runner = mock.Mock(side_effect=AssertionError("emit must not run"))
    crm = adapter.AimaxCrmTrackPort(
        tracker=tracker, db_path=db, command_runner=command_runner
    )
    provider_runner = mock.Mock(side_effect=AssertionError("provider must not run"))
    provider_port = adapter.AsideHeadlessU0Provider(
        runner=provider_runner, clock=lambda: NOW
    )
    slot = datetime(2026, 9, 6, 11, tzinfo=KST)
    manifest.journal.parent.mkdir(parents=True)
    manifest.journal.write_text(
        json.dumps(
            {
                "schema_version": "youtube-shorts-publisher/v1",
                "source_key": manifest.source_key,
                "manifest_fingerprint": manifest.fingerprint,
                "final_mp4_sha256": manifest.video_sha256,
                "status": "provider_verified_crm_pending",
                "slot": slot.isoformat(),
                "attachment": {"reservation_count": 1, "provider_observed_click_count": 1},
                "schedule_commit": {"reservation_count": 1, "provider_observed_click_count": 1},
                "crm": {"reservation_count": 1, "provider_observed_count": 0},
                "verified": {
                    "provider_id": PROVIDER_ID,
                    "shorts_url": f"https://www.youtube.com/shorts/{PROVIDER_ID}",
                },
            }
        ),
        encoding="utf-8",
    )
    runner = publisher.YouTubeShortsPublisher(
        provider_port,
        crm,
        lock_path=tmp_path / "provider.lock",
        local_gate=lambda _video: {"video_sha256": manifest.video_sha256},
        clock=lambda: NOW,
    )

    result = runner.run(manifest_path, now=NOW)

    assert result["status"] == "complete"
    assert result["crm"] == "reconciled"
    provider_runner.assert_not_called()
    command_runner.assert_not_called()
    completed = json.loads(manifest.journal.read_text(encoding="utf-8"))
    assert completed["status"] == "complete"
    assert completed["crm"]["provider_observed_count"] == 1


def test_dry_validate_has_no_aside_studio_or_crm_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, manifest = make_manifest(tmp_path)
    local_gate = mock.Mock(return_value={"video_sha256": manifest.video_sha256})
    monkeypatch.setattr(publisher, "validate_local_candidate", local_gate)
    monkeypatch.setattr(
        adapter,
        "_default_aside_runner",
        mock.Mock(side_effect=AssertionError("Aside resolution/access is forbidden")),
    )
    monkeypatch.setattr(
        adapter.AimaxCrmTrackPort,
        "require_live_dependencies",
        mock.Mock(side_effect=AssertionError("CRM access is forbidden")),
    )

    exit_code = adapter.main([str(manifest_path), "--dry-validate"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "pass"
    assert result["provider_access"] is False
    assert result["crm_access"] is False
    assert result["provider"] == {
        "account": "u0",
        "headless": True,
        "lock": "/tmp/aimax-aside-u0-provider.lock",
    }
    local_gate.assert_called_once()
    adapter._default_aside_runner.assert_not_called()
    adapter.AimaxCrmTrackPort.require_live_dependencies.assert_not_called()


def test_live_dependency_failure_is_clear_and_does_not_run_a_command(tmp_path: Path) -> None:
    command_runner = mock.Mock()
    port = adapter.AimaxCrmTrackPort(
        tracker=tmp_path / "missing-tracker",
        db_path=tmp_path / "missing-ledger",
        command_runner=command_runner,
    )
    with pytest.raises(adapter.LiveDependencyError, match="aimax-crm-track is unavailable"):
        port.lookup("external-safe")
    command_runner.assert_not_called()


def test_concrete_ports_bind_to_final_publisher_interface(tmp_path: Path) -> None:
    provider_port = adapter.AsideHeadlessU0Provider(runner=CapturingAsideRunner({}))
    db = tmp_path / "events.sqlite3"
    create_crm_db(db)
    crm = adapter.AimaxCrmTrackPort(
        tracker=make_tracker(tmp_path / "tracker"), db_path=db
    )
    runner = publisher.YouTubeShortsPublisher(
        provider_port,
        crm,
        lock_path=adapter.PROVIDER_LOCK,
    )
    assert runner.provider is provider_port
    assert runner.crm is crm
    assert runner.lock_path == publisher.SHARED_PROVIDER_LOCK == adapter.PROVIDER_LOCK
