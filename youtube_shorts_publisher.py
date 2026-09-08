#!/usr/bin/env python3
"""Fail-closed, manifest-driven YouTube Shorts scheduling coordinator.

This module owns policy, idempotency, locking, duplicate detection, mutation
budgets, recovery fencing, provider verification, and CRM ordering.  A
task-local provider adapter owns the changing YouTube Studio selectors.  The
adapter contract intentionally requires Aside headless account ``u0`` and the
shared provider lock is held by this coordinator around every provider read or
mutation.

The manifest is a JSON object with these required fields::

    {
      "source_key": "elevenCharId",
      "title": "Exact Studio title",
      "description": "Exact Studio description with both canonical original URLs",
      "final_mp4": "shorts/final.mp4",
      "final_mp4_sha256": "...64 lowercase hex...",
      "original_urls": [
        "https://youtu.be/elevenCharId",
        "https://www.youtube.com/watch?v=elevenCharId"
      ],
      "expected_channel": "channel display name",
      "journal": "shorts/provider/youtube_shorts_journal.json"
    }

Relative paths are resolved from the manifest directory.  The local bundle is
always checked by ``content_production_policy.validate_shorts_bundle_for_upload``
before any provider method is called.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from zoneinfo import ZoneInfo

import content_production_policy as policy


KST = ZoneInfo("Asia/Seoul")
ASIDE_ACCOUNT = "u0"
SHARED_PROVIDER_LOCK = Path("/tmp/aimax-aside-u0-provider.lock")
STATES = frozenset({"public", "scheduled", "private", "draft"})
# A row whose bytes are still transferring has no visibility, no schedule and
# can never be a publish target, so it stays outside the four states the scan
# must account for. It still has to be nameable: without that, one in-flight
# upload makes every scan unreadable and blocks all publishing.
UPLOADING_STATE = "uploading"
ROW_STATES = STATES | {UPLOADING_STATE}
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_MAX_AGE = timedelta(minutes=5)
EVIDENCE_FUTURE_SKEW = timedelta(minutes=1)


class PublisherSafetyError(RuntimeError):
    """Base class for a fail-closed publisher decision."""


class ManifestError(PublisherSafetyError):
    """The manifest or its local candidate is invalid."""


class InventoryError(PublisherSafetyError):
    """A provider inventory is incomplete or contradictory."""


class DuplicateFound(PublisherSafetyError):
    """At least one existing provider row matches this source."""


class AmbiguousProviderState(PublisherSafetyError):
    """A mutation may have happened and must not be repeated."""


class ManualRemediationRequired(PublisherSafetyError):
    """Studio cannot be safely recovered with the proven controls."""


class ScheduleControlUnavailable(ManualRemediationRequired):
    """The direct draft/edit view has no verified visibility control."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _editor_text(value: str) -> str:
    """Normalize provider-safe representation without erasing semantic layout."""

    value = unicodedata.normalize("NFC", value or "")
    value = re.sub(r"[\u200b-\u200d\u2060\ufeff]", "", value)
    return value.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")


def _exact_url_present(text: str, url: str) -> bool:
    """Require a standalone URL token, not a prefix of a query/fragment URL."""

    pattern = rf"(?<!\S){re.escape(url)}(?!\S)"
    return re.search(pattern, text) is not None


def _source_token_present(text: str, source_key: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(source_key)}(?![A-Za-z0-9_-])"
    return re.search(pattern, text) is not None


def _parse_datetime(value: object, *, label: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise InventoryError(f"{label} is not an ISO datetime") from exc
    if result.tzinfo is None or result.utcoffset() != timedelta(hours=9):
        raise InventoryError(f"{label} must carry an explicit KST/GMT+09 offset")
    return result.astimezone(KST)


def _parse_date(value: object, *, label: str) -> date:
    try:
        result = date.fromisoformat(str(value))
    except ValueError as exc:
        raise InventoryError(f"{label} is not an ISO date") from exc
    return result


def _validate_fresh_evidence(captured_at: datetime, now: datetime, *, label: str) -> None:
    if now.tzinfo is None or getattr(now.tzinfo, "key", None) != "Asia/Seoul":
        raise InventoryError("evidence comparison time must use Asia/Seoul")
    age = now - captured_at
    if age > EVIDENCE_MAX_AGE or age < -EVIDENCE_FUTURE_SKEW:
        raise InventoryError(f"{label} is not fresh evidence")


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write and fsync a complete JSON replacement, never a partial journal."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o644)
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        if temp.exists():
            temp.unlink()
        raise


@dataclass(frozen=True)
class PublishManifest:
    source_key: str
    title: str
    description: str
    video: Path
    video_sha256: str
    original_urls: tuple[str, ...]
    expected_channel: str
    journal: Path
    replacement: Mapping[str, Any] | None = None

    @property
    def canonical_urls(self) -> tuple[str, str]:
        return (
            f"https://youtu.be/{self.source_key}",
            f"https://www.youtube.com/watch?v={self.source_key}",
        )

    @property
    def draft_sentinel(self) -> str:
        return f"shorts-{self.source_key}-{self.video_sha256[:12]}"

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "source_key": self.source_key,
                "title": self.title,
                "description": self.description,
                "video": str(self.video),
                "video_sha256": self.video_sha256,
                "original_urls": self.original_urls,
                "expected_channel": self.expected_channel,
                **({"replacement": self.replacement} if self.replacement else {}),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_manifest(path: str | Path) -> PublishManifest:
    manifest_path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"manifest is unreadable: {manifest_path}") from exc
    if not isinstance(raw, dict):
        raise ManifestError("manifest must be a JSON object")
    required = {
        "source_key", "title", "description", "final_mp4", "final_mp4_sha256",
        "original_urls", "expected_channel", "journal",
    }
    missing = sorted(required - set(raw))
    if missing:
        raise ManifestError("manifest fields are missing: " + ", ".join(missing))

    source_key = str(raw["source_key"]).strip()
    if not VIDEO_ID_RE.fullmatch(source_key):
        raise ManifestError("source_key must be an exact 11-character YouTube ID")
    title = str(raw["title"]).strip()
    description = str(raw["description"]).strip()
    if not title or len(title) > 100 or re.search(r"#shorts\b", title, re.I):
        raise ManifestError("title is empty, too long, or contains forbidden #Shorts")
    if not description or len(description) > 5000:
        raise ManifestError("description is empty or exceeds 5000 characters")
    digest = str(raw["final_mp4_sha256"]).strip()
    if not SHA256_RE.fullmatch(digest):
        raise ManifestError("final_mp4_sha256 must be 64 lowercase hex characters")

    base = manifest_path.parent
    video = Path(str(raw["final_mp4"])).expanduser()
    journal = Path(str(raw["journal"])).expanduser()
    video = (base / video).resolve() if not video.is_absolute() else video.resolve()
    journal = (base / journal).resolve() if not journal.is_absolute() else journal.resolve()
    if video.name != "final.mp4":
        raise ManifestError("the upload candidate must be named final.mp4")

    urls_value = raw["original_urls"]
    if not isinstance(urls_value, list):
        raise ManifestError("original_urls must be a list")
    original_urls = tuple(str(value).strip() for value in urls_value)
    canonical = (
        f"https://youtu.be/{source_key}",
        f"https://www.youtube.com/watch?v={source_key}",
    )
    if len(original_urls) != 2 or set(original_urls) != set(canonical):
        raise ManifestError("original_urls must contain exactly both canonical source URLs")
    if any(not _exact_url_present(description, value) for value in canonical):
        raise ManifestError("description must contain both canonical source URLs exactly")
    channel = str(raw["expected_channel"]).strip()
    if not channel:
        raise ManifestError("expected_channel is empty")
    return PublishManifest(
        source_key=source_key,
        title=title,
        description=description,
        video=video,
        video_sha256=digest,
        original_urls=original_urls,
        expected_channel=channel,
        journal=journal,
        replacement=raw.get("replacement"),
    )


def validate_local_candidate(
    manifest: PublishManifest,
    *,
    gate: Callable[[str | Path], Mapping[str, str]] = policy.validate_shorts_bundle_for_upload,
) -> Mapping[str, str]:
    if not manifest.video.is_file() or manifest.video.stat().st_size <= 0:
        raise ManifestError(f"final.mp4 is missing or empty: {manifest.video}")
    actual = _sha256(manifest.video)
    if actual != manifest.video_sha256:
        raise ManifestError("final.mp4 hash differs from the manifest")
    try:
        evidence = gate(manifest.video)
    except Exception as exc:
        raise ManifestError("SHORTS_SPEC/content_production_policy local gate failed") from exc
    if evidence.get("video_sha256") != actual:
        raise ManifestError("local gate is not hash-bound to this final.mp4")
    return evidence


@dataclass(frozen=True)
class ProviderRow:
    identity: str
    provider_id: str
    status: str
    title: str
    description: str
    urls: tuple[str, ...]
    page: int
    direct_metadata_inspected: bool
    scheduled_at: datetime | None
    published_at: datetime | None
    visibility_control_present: bool | None
    published_date: date | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProviderRow":
        identity = str(value.get("identity") or "").strip()
        provider_id = str(value.get("provider_id") or "").strip()
        status = str(value.get("status") or "").lower().strip()
        if not identity or status not in ROW_STATES:
            raise InventoryError("every row needs an identity and a recognized visibility state")
        if provider_id and not VIDEO_ID_RE.fullmatch(provider_id):
            raise InventoryError("provider row contains an invalid video ID")
        if not provider_id and status != "draft":
            raise InventoryError("only a draft may lack a provider ID")
        if value.get("direct_metadata_inspected") is not True:
            raise InventoryError("every row needs direct metadata inspection")
        try:
            page = int(value.get("page"))
        except (TypeError, ValueError) as exc:
            raise InventoryError("provider row page is invalid") from exc
        if page <= 0:
            raise InventoryError("provider row page must be positive")
        scheduled_at = None
        if status == "scheduled":
            scheduled_at = _parse_datetime(value.get("scheduled_at"), label="scheduled_at")
        published_at = None
        published_date = None
        if status == "public":
            if value.get("published_at") not in (None, ""):
                published_at = _parse_datetime(value.get("published_at"), label="published_at")
                published_date = published_at.date()
            else:
                published_date = _parse_date(value.get("published_date"), label="published_date")
        urls = value.get("urls") or []
        if not isinstance(urls, list):
            raise InventoryError("row urls must be a list")
        visibility = value.get("visibility_control_present")
        if visibility not in (True, False, None):
            raise InventoryError("visibility_control_present must be boolean or null")
        return cls(
            identity=identity,
            provider_id=provider_id,
            status=status,
            title=str(value.get("title") or "").strip(),
            description=str(value.get("description") or "").strip(),
            urls=tuple(str(item) for item in urls),
            page=page,
            direct_metadata_inspected=True,
            scheduled_at=scheduled_at,
            published_at=published_at,
            visibility_control_present=visibility,
            published_date=published_date,
        )

    def matches(self, manifest: PublishManifest) -> bool:
        searchable = "\n".join((self.title, self.description, *self.urls))
        exact_title = _editor_text(self.title) == _editor_text(manifest.title)
        source_key = _source_token_present(searchable, manifest.source_key)
        exact_url = any(_exact_url_present(searchable, url) for url in manifest.canonical_urls)
        sentinel = self.status == "draft" and self.title == manifest.draft_sentinel
        return exact_title or source_key or exact_url or sentinel


@dataclass(frozen=True)
class ProviderInventory:
    scan_id: str
    rows: tuple[ProviderRow, ...]
    pages_scanned: int
    captured_at: datetime
    account: str
    headless: bool
    channel: str
    phase: str

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        manifest: PublishManifest,
        now: datetime,
        phase: str,
    ) -> "ProviderInventory":
        if value.get("account") != ASIDE_ACCOUNT or value.get("headless") is not True:
            raise InventoryError("inventory evidence is not bound to Aside headless account u0")
        if value.get("channel") != manifest.expected_channel:
            raise InventoryError("inventory evidence is not bound to expected_channel")
        if value.get("phase") != phase:
            raise InventoryError("inventory evidence phase does not match the requested scan")
        if value.get("status") != "pass":
            raise InventoryError("provider inventory status is not pass")
        if value.get("pagination_complete") is not True or value.get("terminal_reason") != "next_disabled":
            raise InventoryError("provider pagination did not reach the disabled final-page control")
        scanned_states = value.get("scanned_states") or []
        if set(scanned_states) != STATES:
            raise InventoryError("inventory must scan public, scheduled, private, and draft states")
        try:
            pages = int(value.get("pages_scanned"))
        except (TypeError, ValueError) as exc:
            raise InventoryError("pages_scanned is invalid") from exc
        if pages <= 0:
            raise InventoryError("provider inventory scanned no pages")
        rows_value = value.get("rows")
        if not isinstance(rows_value, list):
            raise InventoryError("provider inventory rows are missing")
        rows = tuple(ProviderRow.from_mapping(row) for row in rows_value)
        identities = [row.identity for row in rows]
        if len(set(identities)) != len(identities):
            raise InventoryError("provider inventory contains duplicate row identities")
        if any(row.page > pages for row in rows):
            raise InventoryError("provider row references a page that was not scanned")
        counts = value.get("status_counts") or {}
        actual_counts = {state: sum(row.status == state for row in rows) for state in STATES}
        if {state: int(counts.get(state, -1)) for state in STATES} != actual_counts:
            raise InventoryError("status counts do not account for every provider row")
        uploading = sum(row.status == UPLOADING_STATE for row in rows)
        if sum(actual_counts.values()) + uploading != len(rows):
            raise InventoryError("status counts do not account for every provider row")
        if uploading and int(value.get("uploading_rows", -1)) != uploading:
            raise InventoryError("in-flight upload rows are not reported by the scan")
        captured = _parse_datetime(value.get("captured_at"), label="captured_at")
        _validate_fresh_evidence(captured, now, label="inventory captured_at")
        if any(row.published_at and row.published_at > captured for row in rows):
            raise InventoryError("public published_at cannot be later than inventory captured_at")
        if any(row.published_date and row.published_date > captured.date() for row in rows):
            raise InventoryError("public published_date cannot be later than inventory captured_at")
        scan_id = str(value.get("scan_id") or "").strip()
        if not scan_id:
            raise InventoryError("inventory scan_id is missing")
        return cls(
            scan_id=scan_id,
            rows=rows,
            pages_scanned=pages,
            captured_at=captured,
            account=ASIDE_ACCOUNT,
            headless=True,
            channel=manifest.expected_channel,
            phase=phase,
        )

    def matches(self, manifest: PublishManifest) -> tuple[ProviderRow, ...]:
        return tuple(row for row in self.rows if row.matches(manifest))

    def occupancy_slots(self, now: datetime) -> list[datetime]:
        """Return the deterministic current/future schedule-policy window.

        Historical public rows remain duplicate evidence but do not participate
        in future planning.  Today's already-public Shorts do participate, as
        do scheduled rows from today onward.
        """

        if now.tzinfo is None or getattr(now.tzinfo, "key", None) != "Asia/Seoul":
            raise InventoryError("planner scope requires Asia/Seoul")
        day = now.date()
        slots = [
            row.scheduled_at
            for row in self.rows
            if row.status == "scheduled" and row.scheduled_at is not None and row.scheduled_at.date() >= day
        ]
        slots.extend(
            row.published_at
            for row in self.rows
            if row.status == "public" and row.published_at is not None and row.published_at.date() == day
        )
        try:
            return policy.validate_schedule(value for value in slots if value is not None)
        except Exception as exc:
            raise InventoryError("provider occupancy violates the Shorts SSOT") from exc

    def fingerprint(self) -> str:
        payload = [
            (
                row.identity,
                row.provider_id,
                row.status,
                row.scheduled_at.isoformat() if row.scheduled_at else "",
                row.published_at.isoformat() if row.published_at else "",
                row.published_date.isoformat() if row.published_date else "",
            )
            for row in self.rows
        ]
        return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()


class ProviderPort(Protocol):
    account: str
    headless: bool

    def scan_inventory(self, manifest: PublishManifest, *, phase: str) -> Mapping[str, Any]: ...

    def attach_once(
        self, manifest: PublishManifest, *, draft_sentinel: str
    ) -> Mapping[str, Any]: ...

    def schedule_once(
        self, manifest: PublishManifest, row: ProviderRow, slot: datetime
    ) -> Mapping[str, Any]: ...

    def direct_requery(
        self, manifest: PublishManifest, provider_id: str
    ) -> Mapping[str, Any]: ...


class CrmPort(Protocol):
    def lookup(self, dedupe_key: str) -> Mapping[str, Any] | None: ...

    def emit(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


class AtomicJournal:
    def __init__(self, path: Path, manifest: PublishManifest):
        self.path = path
        self.manifest = manifest

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AmbiguousProviderState("journal is unreadable; recover manually") from exc
        if not isinstance(value, dict):
            raise AmbiguousProviderState("journal is not an object")
        if (
            value.get("source_key") != self.manifest.source_key
            or value.get("manifest_fingerprint") != self.manifest.fingerprint
            or value.get("final_mp4_sha256") != self.manifest.video_sha256
        ):
            raise AmbiguousProviderState("journal belongs to another source or candidate")
        return value

    def write(self, value: dict[str, Any]) -> None:
        _atomic_write_json(self.path, value)

    def create(self, inventory: ProviderInventory, slot: datetime) -> dict[str, Any]:
        if self.path.exists():
            raise AmbiguousProviderState("journal appeared concurrently")
        now = datetime.now(KST).isoformat()
        value: dict[str, Any] = {
            "schema_version": "youtube-shorts-publisher/v1",
            "source_key": self.manifest.source_key,
            "manifest_fingerprint": self.manifest.fingerprint,
            "final_mp4_sha256": self.manifest.video_sha256,
            "status": "precommit_verified",
            "slot": slot.isoformat(),
            "baseline_identities": [row.identity for row in inventory.rows],
            "precommit_scan_id": inventory.scan_id,
            "precommit_inventory_fingerprint": inventory.fingerprint(),
            "attachment": {"reservation_count": 0, "provider_observed_click_count": 0},
            "schedule_commit": {"reservation_count": 0, "provider_observed_click_count": 0},
            "crm": {"reservation_count": 0, "provider_observed_count": 0},
            "created_at": now,
            "updated_at": now,
            "history": [{"event": "precommit_requery_passed", "at": now}],
        }
        self.write(value)
        return value

    def event(self, value: dict[str, Any], event: str, **fields: Any) -> None:
        now = datetime.now(KST).isoformat()
        item = {"event": event, "at": now, **fields}
        value.setdefault("history", []).append(item)
        value["updated_at"] = now
        self.write(value)

    def reserve(self, value: dict[str, Any], action: str) -> None:
        counter = value[action]
        if int(counter.get("reservation_count") or 0) != 0:
            raise AmbiguousProviderState(f"{action} was already reserved; never repeat it")
        counter["reservation_count"] = 1
        value["status"] = f"{action}_invocation_started_do_not_repeat"
        self.event(value, f"{action}_reserved")

    def observe(self, value: dict[str, Any], action: str, count: int) -> None:
        if count not in (0, 1):
            raise AmbiguousProviderState(f"{action} provider click count exceeds the one-click budget")
        if int(value[action].get("reservation_count") or 0) != 1:
            raise AmbiguousProviderState(f"{action} observation lacks a reservation")
        value[action]["provider_observed_click_count"] = count
        self.event(value, f"{action}_provider_observation", count=count)


def _slot_from_journal(value: Mapping[str, Any]) -> datetime:
    return _parse_datetime(value.get("slot"), label="journal slot")


def _crm_dedupe_key(manifest: PublishManifest) -> str:
    value = f"youtube_shorts:youtube-content-repurpose:sent:{manifest.source_key}"
    return "external-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _crm_payload(manifest: PublishManifest) -> dict[str, Any]:
    """Return the complete shared-ledger payload; it contains no content or URL."""

    return {
        "channel": "youtube_shorts",
        "campaign": "youtube-content-repurpose",
        "stage": "sent",
        "status": "success",
        "count": 1,
        "dedupe_key": _crm_dedupe_key(manifest),
    }


def _validate_crm_receipt(receipt: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    receipt_count = receipt.get("count", receipt.get("metric_count"))
    checks = {
        "channel": receipt.get("channel") == payload["channel"],
        "campaign": receipt.get("campaign") == payload["campaign"],
        "stage": receipt.get("stage") == payload["stage"],
        "status": receipt.get("status") == payload["status"],
        "count": receipt_count == payload["count"],
        "dedupe_key": receipt.get("dedupe_key") == payload["dedupe_key"],
    }
    if not all(checks.values()):
        failed = ", ".join(key for key, passed in checks.items() if not passed)
        raise AmbiguousProviderState(f"CRM did not confirm the exact sent event: {failed}")


def validate_direct_evidence(
    evidence: Mapping[str, Any],
    manifest: PublishManifest,
    provider_id: str,
    slot: datetime,
    *,
    now: datetime,
) -> dict[str, Any]:
    canonical_url = f"https://www.youtube.com/shorts/{provider_id}"
    direct_description = str(evidence.get("description") or "")
    raw_urls = evidence.get("original_urls")
    exact_original_urls = (
        isinstance(raw_urls, list)
        and len(raw_urls) == 2
        and set(str(value) for value in raw_urls) == set(manifest.canonical_urls)
    )
    try:
        captured_at = _parse_datetime(evidence.get("captured_at"), label="direct captured_at")
        _validate_fresh_evidence(captured_at, now, label="direct captured_at")
        fresh = True
    except InventoryError:
        captured_at = None
        fresh = False
    checks = {
        "account": evidence.get("account") == ASIDE_ACCOUNT,
        "headless": evidence.get("headless") is True,
        "channel": evidence.get("channel") == manifest.expected_channel,
        "fresh": fresh,
        "query_id": bool(str(evidence.get("query_id") or "").strip()),
        "provider_id": evidence.get("provider_id") == provider_id and bool(VIDEO_ID_RE.fullmatch(provider_id)),
        "shorts_url": evidence.get("shorts_url") == canonical_url,
        "single_row": evidence.get("exact_row_count") == 1,
        "title": _editor_text(str(evidence.get("title") or "")) == _editor_text(manifest.title),
        # The edit form reports what was typed into it, saved or not, so the form
        # title alone once let seven shorts publish under their upload sentinel.
        # The video list carries the provider's own stored title.
        "list_row_title": _editor_text(str(evidence.get("list_row_title") or "")) == _editor_text(manifest.title),
        "description": _editor_text(direct_description) == _editor_text(manifest.description),
        "description_original_urls": all(
            _exact_url_present(direct_description, url) for url in manifest.canonical_urls
        ),
        "original_urls": exact_original_urls,
        "no_kids": evidence.get("no_kids") is True,
        "status": evidence.get("status") == "scheduled",
        "timezone": evidence.get("timezone") == "Asia/Seoul",
    }
    try:
        direct_slot = _parse_datetime(evidence.get("scheduled_at"), label="direct scheduled_at")
    except InventoryError:
        direct_slot = None
    checks["date_time"] = direct_slot == slot
    if not all(checks.values()):
        failed = ", ".join(key for key, passed in checks.items() if not passed)
        raise AmbiguousProviderState(f"direct provider requery failed: {failed}")
    return {
        "provider_id": provider_id,
        "shorts_url": canonical_url,
        "scheduled_at": slot.isoformat(),
        "timezone": "Asia/Seoul",
        "captured_at": captured_at.isoformat() if captured_at else None,
        "query_id": str(evidence.get("query_id") or "").strip(),
        "account": ASIDE_ACCOUNT,
        "headless": True,
        "channel": manifest.expected_channel,
        "checks": checks,
    }


class YouTubeShortsPublisher:
    """Coordinate exactly-once provider scheduling through injected ports."""

    def __init__(
        self,
        provider: ProviderPort,
        crm: CrmPort,
        *,
        lock_path: Path = SHARED_PROVIDER_LOCK,
        local_gate: Callable[[str | Path], Mapping[str, str]] = policy.validate_shorts_bundle_for_upload,
        clock: Callable[[], datetime] | None = None,
    ):
        if provider.account != ASIDE_ACCOUNT or provider.headless is not True:
            raise PublisherSafetyError("provider must be Aside headless account u0")
        self.provider = provider
        self.crm = crm
        self.lock_path = lock_path
        self.crm_lock_path = lock_path.with_name(f"{lock_path.name}.crm")
        self.local_gate = local_gate
        self.clock = clock or (lambda: datetime.now(KST))

    def _sample_now(self, fixed_now: datetime | None) -> datetime:
        current = fixed_now if fixed_now is not None else self.clock()
        if current.tzinfo is None or getattr(current.tzinfo, "key", None) != "Asia/Seoul":
            raise PublisherSafetyError("now must use the Asia/Seoul timezone")
        return current

    def _scan(
        self,
        manifest: PublishManifest,
        phase: str,
        fixed_now: datetime | None,
    ) -> ProviderInventory:
        evidence = self.provider.scan_inventory(manifest, phase=phase)
        return ProviderInventory.from_mapping(
            evidence,
            manifest=manifest,
            now=self._sample_now(fixed_now),
            phase=phase,
        )

    @staticmethod
    def _require_distinct_scan(
        previous: ProviderInventory,
        current: ProviderInventory,
        *,
        label: str,
    ) -> None:
        if current.scan_id == previous.scan_id:
            raise InventoryError(f"{label} scan evidence is not distinct")
        if current.captured_at < previous.captured_at:
            raise InventoryError(f"{label} scan evidence predates the prior scan")
        if current.captured_at == previous.captured_at:
            raise InventoryError(f"{label} scan evidence is not later than the prior scan")

    def _plan(self, inventory: ProviderInventory, now: datetime) -> datetime:
        if now.tzinfo is None or getattr(now.tzinfo, "key", None) != "Asia/Seoul":
            raise PublisherSafetyError("now must use the Asia/Seoul timezone")
        planner_now = now
        # Studio's content list exposes only a KST calendar date for already-
        # public rows.  Without an exact release time, a same-day five-hour gap
        # cannot be proved.  Preserve that provider meaning and conservatively
        # start planning on the next KST day instead of inventing a timestamp.
        if any(
            row.status == "public"
            and row.published_at is None
            and row.published_date == now.date()
            for row in inventory.rows
        ):
            planner_now = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=KST)
        try:
            return policy.plan_shorts_schedule(
                inventory.occupancy_slots(now), planner_now, horizon_days=366
            )
        except Exception as exc:
            raise PublisherSafetyError("SHORTS_SPEC slot planner found no safe KST slot") from exc

    @staticmethod
    def _only_match(inventory: ProviderInventory, manifest: PublishManifest) -> ProviderRow | None:
        matches = inventory.matches(manifest)
        if len(matches) > 1:
            raise AmbiguousProviderState("multiple exact source/title/URL provider rows exist")
        return matches[0] if matches else None

    @staticmethod
    def _recover_attached_row(
        inventory: ProviderInventory,
        manifest: PublishManifest,
        journal: Mapping[str, Any],
    ) -> ProviderRow:
        exact = inventory.matches(manifest)
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise AmbiguousProviderState("multiple exact provider targets exist after attachment")
        baseline = set(journal.get("baseline_identities") or ())
        delta = [row for row in inventory.rows if row.identity not in baseline]
        sentinel = [row for row in delta if row.status == "draft" and row.title == manifest.draft_sentinel]
        if len(delta) != 1 or len(sentinel) != 1:
            raise AmbiguousProviderState("post-attachment provider delta is not one exact sentinel draft")
        return sentinel[0]

    def _mark_ambiguous(self, store: AtomicJournal, journal: dict[str, Any], event: str) -> None:
        journal["status"] = "ambiguous_recover_or_verify_only"
        store.event(journal, event)

    def _schedule_recovered(
        self,
        manifest: PublishManifest,
        store: AtomicJournal,
        journal: dict[str, Any],
        current: ProviderInventory,
        planned_at: datetime,
        fixed_now: datetime | None,
    ) -> tuple[ProviderRow, datetime]:
        row = self._recover_attached_row(current, manifest, journal)
        slot = _slot_from_journal(journal)
        if row.status == "scheduled":
            return row, slot
        if row.status not in {"draft", "private"}:
            self._mark_ambiguous(store, journal, "target_state_not_recoverable")
            raise AmbiguousProviderState(f"attached target has unrecoverable state: {row.status}")
        # Explicit requery immediately before the only allowed schedule click.
        precommit = self._scan(manifest, "schedule_precommit_requery", fixed_now)
        self._require_distinct_scan(current, precommit, label="schedule precommit")
        row = self._recover_attached_row(precommit, manifest, journal)
        if row.status == "scheduled":
            return row, slot
        schedule_now = self._sample_now(fixed_now)
        occupancy = precommit.occupancy_slots(schedule_now)
        replan_reasons: list[str] = []
        if slot <= schedule_now:
            replan_reasons.append("reserved_slot_elapsed")
        if schedule_now.date() != planned_at.date():
            replan_reasons.append("kst_date_boundary_crossed")
        slot_conflict = False
        try:
            policy.validate_schedule([*occupancy, slot])
        except Exception:
            slot_conflict = True
        if slot_conflict:
            if not replan_reasons:
                journal["status"] = "blocked_manual_reserved_slot_conflict"
                store.event(journal, "manual_remediation_required", reason="reserved_slot_conflict")
                raise ManualRemediationRequired("the journaled slot now conflicts with provider state")
            replan_reasons.append("reserved_slot_conflict")
        if replan_reasons:
            previous_slot = slot
            slot = self._plan(precommit, schedule_now)
            journal["slot"] = slot.isoformat()
            store.event(
                journal,
                "slot_replanned_before_schedule",
                previous_slot=previous_slot.isoformat(),
                slot=slot.isoformat(),
                reasons=sorted(set(replan_reasons)),
            )
        if slot <= schedule_now:
            journal["status"] = "blocked_manual_reserved_slot_elapsed"
            store.event(journal, "manual_remediation_required", reason="replanned_slot_elapsed")
            raise ManualRemediationRequired("freshly planned slot is not in the future")

        store.reserve(journal, "schedule_commit")
        try:
            receipt = self.provider.schedule_once(manifest, row, slot)
        except ScheduleControlUnavailable:
            journal["status"] = "blocked_manual_visibility_control_missing"
            store.event(journal, "manual_remediation_required", reason="visibility_control_missing")
            raise
        except Exception as exc:
            self._mark_ambiguous(store, journal, "schedule_invocation_interrupted")
            raise AmbiguousProviderState("schedule invocation was interrupted; never reclick") from exc
        count = int(receipt.get("provider_observed_schedule_click_count") or 0)
        store.observe(journal, "schedule_commit", count)
        if receipt.get("status") != "committed" or count != 1:
            self._mark_ambiguous(store, journal, "schedule_receipt_ambiguous")
            raise AmbiguousProviderState("schedule receipt is ambiguous; never reclick")
        journal["status"] = "schedule_click_observed_verify_only"
        store.event(journal, "schedule_click_observed")
        post = self._scan(manifest, "post_schedule_requery", fixed_now)
        self._require_distinct_scan(precommit, post, label="post-schedule")
        target = self._only_match(post, manifest)
        if target is None or target.status != "scheduled" or not target.provider_id:
            self._mark_ambiguous(store, journal, "post_schedule_target_missing")
            raise AmbiguousProviderState("post-schedule inventory lacks one exact scheduled row")
        return target, slot

    def _verify(
        self,
        manifest: PublishManifest,
        store: AtomicJournal,
        journal: dict[str, Any],
        row: ProviderRow,
        slot: datetime,
        fixed_now: datetime | None,
    ) -> None:
        if not row.provider_id:
            self._mark_ambiguous(store, journal, "provider_id_missing")
            raise AmbiguousProviderState("direct verification requires an exact provider ID")
        try:
            direct = self.provider.direct_requery(manifest, row.provider_id)
            verified = validate_direct_evidence(
                direct,
                manifest,
                row.provider_id,
                slot,
                now=self._sample_now(fixed_now),
            )
        except Exception as exc:
            self._mark_ambiguous(store, journal, "direct_requery_failed")
            if isinstance(exc, PublisherSafetyError):
                raise
            raise AmbiguousProviderState("direct provider requery was interrupted") from exc
        journal["verified"] = verified
        journal["status"] = "provider_verified_crm_pending"
        store.event(journal, "provider_directly_verified")

    def _finish_crm(
        self, manifest: PublishManifest, store: AtomicJournal, journal: dict[str, Any]
    ) -> dict[str, Any]:
        if journal.get("status") == "complete":
            return {"status": "complete", "provider": journal.get("verified"), "crm": "already_recorded"}
        if not journal.get("verified"):
            raise AmbiguousProviderState("CRM is fenced until direct provider verification")
        payload = _crm_payload(manifest)
        existing = self.crm.lookup(payload["dedupe_key"])
        if existing is not None:
            _validate_crm_receipt(existing, payload)
            journal["crm"]["provider_observed_count"] = 1
            journal["status"] = "complete"
            store.event(journal, "crm_reconciled_by_dedupe")
            return {"status": "complete", "provider": journal["verified"], "crm": "reconciled"}
        if int(journal["crm"].get("reservation_count") or 0) != 0:
            journal["status"] = "provider_verified_crm_ambiguous_do_not_reemit"
            store.event(journal, "crm_unconfirmed_after_reserved_invocation")
            raise AmbiguousProviderState("CRM invocation was reserved but is not in the ledger; never re-emit blindly")
        store.reserve(journal, "crm")
        try:
            receipt = self.crm.emit(payload)
        except Exception as exc:
            journal["status"] = "provider_verified_crm_ambiguous_do_not_reemit"
            store.event(journal, "crm_invocation_interrupted")
            raise AmbiguousProviderState("CRM invocation was interrupted; reconcile by dedupe only") from exc
        _validate_crm_receipt(receipt, payload)
        journal["crm"]["provider_observed_count"] = 1
        journal["status"] = "complete"
        store.event(journal, "crm_verified_after_provider")
        return {"status": "complete", "provider": journal["verified"], "crm": "emitted"}

    def run(self, manifest_path: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
        manifest = load_manifest(manifest_path)
        if manifest.replacement and not getattr(self, "supports_replacement", False):
            raise ManifestError("Replacement requires the explicit replacement coordinator")
        validate_local_candidate(manifest, gate=self.local_gate)
        fixed_now = now
        current = self._sample_now(fixed_now)
        store = AtomicJournal(manifest.journal, manifest)
        journal = store.load()
        if journal and journal.get("status") == "complete":
            return {"status": "complete", "provider": journal.get("verified"), "crm": "already_recorded"}
        if journal and journal.get("status") == "blocked_manual_visibility_control_missing":
            raise ScheduleControlUnavailable(
                "current Studio direct draft has no verified visibility control; manual remediation required"
            )

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            journal = store.load()
            if journal and journal.get("status") == "complete":
                return {"status": "complete", "provider": journal.get("verified"), "crm": "already_recorded"}
            if journal and journal.get("status") == "blocked_manual_visibility_control_missing":
                raise ScheduleControlUnavailable(
                    "current Studio direct draft has no verified visibility control; manual remediation required"
                )
            if journal and journal.get("status") == "provider_verified_crm_pending":
                pass
            else:
                inventory = self._scan(manifest, "initial_recovery_or_duplicate_scan", fixed_now)
                if journal is None:
                    matches = inventory.matches(manifest)
                    if matches:
                        if len(matches) > 1:
                            raise AmbiguousProviderState(
                                "multiple exact source/title/URL provider rows exist; verify only"
                            )
                        states = ",".join(sorted(row.status for row in matches))
                        raise DuplicateFound(f"existing exact provider rows block upload: {states}")
                    # The second complete scan is the explicit precommit requery.
                    precommit = self._scan(manifest, "attachment_precommit_requery", fixed_now)
                    self._require_distinct_scan(inventory, precommit, label="attachment precommit")
                    matches = precommit.matches(manifest)
                    if matches:
                        if len(matches) > 1:
                            raise AmbiguousProviderState(
                                "precommit found multiple exact provider rows; verify only"
                            )
                        states = ",".join(sorted(row.status for row in matches))
                        raise DuplicateFound(f"precommit found an exact provider row: {states}")
                    planned_at = self._sample_now(fixed_now)
                    slot = self._plan(precommit, planned_at)
                    journal = store.create(precommit, slot)
                    store.reserve(journal, "attachment")
                    try:
                        receipt = self.provider.attach_once(manifest, draft_sentinel=manifest.draft_sentinel)
                    except Exception as exc:
                        self._mark_ambiguous(store, journal, "attachment_invocation_interrupted")
                        raise AmbiguousProviderState("attachment invocation was interrupted; never reupload") from exc
                    count = int(receipt.get("provider_observed_attachment_click_count") or 0)
                    store.observe(journal, "attachment", count)
                    if receipt.get("status") != "attached" or count != 1:
                        self._mark_ambiguous(store, journal, "attachment_receipt_ambiguous")
                        raise AmbiguousProviderState("attachment receipt is ambiguous; never reupload")
                    journal["status"] = "attachment_observed_recover_only"
                    store.event(journal, "attachment_observed")
                    inventory = self._scan(manifest, "post_attachment_recovery_scan", fixed_now)
                    self._require_distinct_scan(precommit, inventory, label="post-attachment")
                else:
                    planned_at = current
                    # Every journaled attachment reservation permanently fences reupload.
                    attachment = journal.get("attachment") or {}
                    if int(attachment.get("reservation_count") or 0) != 1:
                        raise AmbiguousProviderState("journal exists without exactly one attachment reservation")

                schedule = journal.get("schedule_commit") or {}
                if int(schedule.get("reservation_count") or 0) == 1:
                    # Ambiguous or observed schedule attempt: only scan and direct-verify; never reclick.
                    target = self._only_match(inventory, manifest)
                    if target is None or target.status != "scheduled" or not target.provider_id:
                        self._mark_ambiguous(store, journal, "schedule_recovery_not_verified")
                        raise AmbiguousProviderState("reserved schedule click is not directly recoverable; never reclick")
                    slot = _slot_from_journal(journal)
                else:
                    target, slot = self._schedule_recovered(
                        manifest,
                        store,
                        journal,
                        inventory,
                        planned_at,
                        fixed_now,
                    )
                self._verify(manifest, store, journal, target, slot, fixed_now)

        # CRM is outside the provider lock, but its complete exactly-once
        # transaction is serialized across journals sharing this provider lock.
        self.crm_lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.crm_lock_path.open("a+") as crm_lock:
            fcntl.flock(crm_lock.fileno(), fcntl.LOCK_EX)
            journal = store.load()
            if journal is None:
                raise AmbiguousProviderState("journal disappeared after provider verification")
            return self._finish_crm(manifest, store, journal)


__all__ = [
    "ASIDE_ACCOUNT",
    "SHARED_PROVIDER_LOCK",
    "STATES",
    "AmbiguousProviderState",
    "CrmPort",
    "DuplicateFound",
    "InventoryError",
    "ManualRemediationRequired",
    "ManifestError",
    "ProviderInventory",
    "ProviderPort",
    "ProviderRow",
    "PublishManifest",
    "PublisherSafetyError",
    "ScheduleControlUnavailable",
    "YouTubeShortsPublisher",
    "load_manifest",
    "validate_direct_evidence",
    "validate_local_candidate",
]
