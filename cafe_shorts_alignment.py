"""Read-only Cafe ordering from verified, same-source YouTube Shorts evidence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlparse


SHORT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
UNCERTAIN_MARKERS = (
    "12_provider_success_reservation.json",
    "provider_uncertain_do_not_retry.json",
    "published_but_verification_failed_do_not_retry.json",
)
_INVENTORY_CACHE: dict[str, tuple[tuple[tuple[str, int, int], ...], tuple[Path, dict] | None]] = {}


def is_shorts_aligned(queue: dict) -> bool:
    return queue.get("publication_mode") == "shorts_aligned"


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _shorts_url(value: object) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return len(parts) == 2 and parts[0] == "shorts" and bool(SHORT_ID.fullmatch(parts[1]))


def _journal_evidence(path: Path, source_key: str) -> dict | None:
    journal = _read_json(path)
    verified = journal.get("verified")
    if journal.get("status") != "complete" or journal.get("source_key") != source_key:
        return None
    if not isinstance(verified, dict) or not _shorts_url(verified.get("shorts_url")):
        return None
    url_id = urlparse(str(verified["shorts_url"])).path.rsplit("/", 1)[-1]
    if verified.get("provider_id") != url_id:
        return None
    checks = verified.get("checks")
    if not isinstance(checks, dict) or not checks or not all(value is True for value in checks.values()):
        return None
    try:
        scheduled = datetime.fromisoformat(str(verified.get("scheduled_at") or ""))
    except ValueError:
        return None
    if scheduled.tzinfo is None or scheduled.utcoffset() is None:
        return None
    return {
        "scheduled_at": scheduled.isoformat(),
        "url": verified["shorts_url"],
        "provider_id": verified["provider_id"],
        "evidence_path": str(path),
    }


def _scheduling_evidence(path: Path, source_key: str) -> dict | None:
    value = _read_json(path)
    key = value.get("sourceKey") or value.get("source_key")
    if key != source_key or value.get("status") != "scheduled_verified":
        return None
    provider_id = value.get("providerVideoId") or value.get("provider_video_id")
    url = value.get("providerUrl") or value.get("provider_url")
    scheduled = value.get("scheduledAt") or (value.get("targeted_reverify") or {}).get("scheduled_at")
    gates = value.get("gates")
    sealed = value.get("finalDirectReverify") or value.get("final_direct_reverify")
    if (not isinstance(gates, dict) or not gates
            or not all(check is True for check in gates.values())
            or not isinstance(sealed, dict)
            or str(sealed.get("status") or "").upper() != "PASS"
            or (sealed.get("source_key") or sealed.get("sourceKey")) != source_key
            or not isinstance(sealed.get("checks"), dict) or not sealed["checks"]
            or not all(check is True for check in sealed["checks"].values())):
        return None
    if not SHORT_ID.fullmatch(str(provider_id or "")) or not _shorts_url(url):
        return None
    if urlparse(str(url)).path.rsplit("/", 1)[-1] != provider_id:
        return None
    if ((sealed.get("provider_video_id") or sealed.get("providerVideoId")) != provider_id
            or (sealed.get("provider_url") or sealed.get("providerUrl")) != url):
        return None
    try:
        stamp = datetime.fromisoformat(str(scheduled or ""))
    except ValueError:
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return {"scheduled_at": stamp.isoformat(), "url": url, "provider_id": provider_id,
            "evidence_path": str(path)}


def _local_evidence(root: Path, source_key: str) -> tuple[dict | None, bool]:
    journal = root / "shorts/provider/journal.json"
    scheduling = root / "shorts/provider/provider_scheduling_evidence.json"
    if journal.exists():
        return _journal_evidence(journal, source_key), True
    if scheduling.exists():
        return _scheduling_evidence(scheduling, source_key), True
    return None, False


def _inventory(project: Path) -> tuple[Path, dict] | None:
    paths = sorted((project / "outputs").glob("*/shorts/inventory-provider-model-*.json"))
    signature_parts = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        signature_parts.append((str(path), stat.st_mtime_ns, stat.st_size))
    signature = tuple(signature_parts)
    cache_key = str(project.resolve())
    cached = _INVENTORY_CACHE.get(cache_key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    # capture() writes captured_at after the rows. Read a small suffix from every
    # snapshot, then fully parse only the newest timestamp group that contains
    # a complete snapshot. This preserves captured-time ordering without
    # repeatedly decoding old multi-megabyte row arrays.
    dated: list[tuple[datetime, Path]] = []
    for path_string, _, size in signature:
        path = Path(path_string)
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, size - 65536))
                prefix = handle.read(65536)
        except OSError:
            continue
        match = re.search(rb'"captured_at"\s*:\s*"([^"\\]+)"', prefix)
        if not match:
            continue
        try:
            captured = datetime.fromisoformat(match.group(1).decode("ascii").replace("Z", "+00:00"))
        except (UnicodeDecodeError, ValueError):
            continue
        if captured.tzinfo is not None:
            dated.append((captured, path))
    dated.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
    candidates = []
    for captured_time in sorted({item[0] for item in dated}, reverse=True):
        group = []
        for _, path in (item for item in dated if item[0] == captured_time):
            value = _read_json(path)
            rows, pages = value.get("rows"), value.get("pages")
            channel_id = value.get("channel_id")
            try:
                captured = datetime.fromisoformat(str(value.get("captured_at") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            complete = (
                value.get("status") == "pass" and captured == captured_time
                and captured.tzinfo is not None
                and isinstance(rows, list) and rows and isinstance(pages, list) and pages
                and pages[-1].get("next_disabled") is True
                and sum(int(page.get("row_count") or 0) for page in pages) == len(rows)
                and isinstance(channel_id, str) and bool(channel_id)
                and all(isinstance(row, dict) and SHORT_ID.fullmatch(str(row.get("provider_id") or ""))
                        and row.get("identity") == row.get("provider_id")
                        and row.get("model_channel_id") == channel_id
                        and row.get("metadata_origin") == "studio_provider_row_model" for row in rows)
                and len({row["provider_id"] for row in rows}) == len(rows)
            )
            if complete:
                group.append((captured, str(path), path, value))
        if group:
            candidates = group
            break
    if not candidates:
        _INVENTORY_CACHE[cache_key] = (signature, None)
        return None
    candidates.sort(reverse=True)
    newest_time = candidates[0][0]
    newest = [item for item in candidates if item[0] == newest_time]
    if len({item[3].get("scan_id") for item in newest}) != 1:
        _INVENTORY_CACHE[cache_key] = (signature, None)
        return None
    result = (newest[0][2], newest[0][3])
    _INVENTORY_CACHE[cache_key] = (signature, result)
    return result


def _fresh_provider(proof: dict, inventory_path: Path, inventory: dict) -> dict | None:
    matches = [row for row in inventory["rows"]
               if row.get("provider_id") == proof.get("provider_id")]
    if len(matches) != 1 or matches[0].get("identity") != proof.get("provider_id"):
        return None
    row = matches[0]
    try:
        if row["status"] == "public":
            if (row.get("privacy") != "VIDEO_PRIVACY_PUBLIC" or row.get("scheduled_raw")
                    or int(row.get("published_seconds") or 0) <= 0):
                return None
            stamp = datetime.fromtimestamp(int(row["published_seconds"]), tz=timezone.utc)
        elif row["status"] == "scheduled":
            if (row.get("privacy") != "VIDEO_PRIVACY_PRIVATE"
                    or row.get("draft_status") != "DRAFT_STATUS_NONE"
                    or int(row.get("published_seconds") or 0) != 0):
                return None
            schedules = (row.get("scheduled_raw") or {}).get("scheduledPublishings") or []
            active = [item for item in schedules
                      if item.get("status") == "SCHEDULED_PUBLISHING_STATUS_SCHEDULED"
                      and item.get("action") == "SCHEDULED_PUBLISHING_ACTION_SET_PUBLIC"]
            if len(active) != 1:
                return None
            stamp = datetime.fromtimestamp(int(active[0]["scheduledTimeSeconds"]), tz=timezone.utc)
        else:
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return {"scheduled_at": stamp.isoformat(), "url": proof["url"],
            "evidence_path": proof["evidence_path"], "inventory_path": str(inventory_path),
            "inventory_captured_at": str(inventory["captured_at"]),
            "provider_status": row["status"], "provider_id": proof["provider_id"]}


def _manifest_root(project: Path, entry: dict) -> Path | None:
    raw = entry.get("manifest")
    if not isinstance(raw, str) or not raw.strip():
        return None
    manifest = Path(raw)
    if not manifest.is_absolute():
        manifest = project / manifest
    # Canonical layout: outputs/<source-date>/cafe/06_cafe_manifest.json.
    if manifest.parent.name != "cafe":
        return None
    return manifest.parent.parent


def _manifest_matches(project: Path, entry: dict, source_key: str) -> bool:
    raw = entry.get("manifest")
    if not isinstance(raw, str) or not raw.strip():
        return False
    path = Path(raw)
    if not path.is_absolute():
        path = project / path
    value = _read_json(path)
    return (value.get("source_key") or value.get("sourceKey")) == source_key


def _verified_shorts(
    project: Path, entry: dict, current_inventory: tuple[Path, dict] | None
) -> dict | None:
    """Return sealed Shorts evidence without trusting advisory queue fields.

    A journal beside the entry's manifest is authoritative. If it exists but is
    malformed or mismatched, older roots cannot hide that conflict. Without a
    manifest journal, the newest root containing a journal is authoritative;
    differing valid evidence in other roots is ambiguous and fails closed.
    """

    source_key = str(entry.get("source_key") or "")
    if not SHORT_ID.fullmatch(source_key):
        return None
    project = Path(project)
    if current_inventory is None:
        return None
    inventory_path, inventory = current_inventory
    root = _manifest_root(project, entry)
    if root is not None:
        proof, exists = _local_evidence(root, source_key)
        if exists:
            if (root / "shorts/provider/provider_scheduling_evidence.json").exists() \
                    and not (root / "shorts/provider/journal.json").exists() \
                    and not _manifest_matches(project, entry, source_key):
                return None
            return _fresh_provider(proof, inventory_path, inventory) if proof else None

    roots = sorted(
        (path for path in (project / "outputs").glob(f"{source_key}-20??????") if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )
    local = []
    for candidate in roots:
        proof, exists = _local_evidence(candidate, source_key)
        # A legacy receipt has no independent root binding. It is usable only
        # beside the queue manifest already checked above, never by discovery.
        if (exists and not (candidate / "shorts/provider/journal.json").exists()
                and (root != candidate or not _manifest_matches(project, entry, source_key))):
            proof = None
        local.append((proof, exists, candidate))
    local = [item for item in local if item[1]]
    if not local:
        return None
    newest = local[0][0]
    if newest is None:
        return None
    signatures = {
        (item["scheduled_at"], item["url"])
        for item in (item[0] for item in local)
        if item is not None
    }
    if signatures != {(newest["scheduled_at"], newest["url"])}:
        return None
    return _fresh_provider(newest, inventory_path, inventory)


def verified_shorts(project: Path, entry: dict) -> dict | None:
    """Return current provider truth for one source using a fresh inventory read."""

    project = Path(project)
    return _verified_shorts(project, entry, _inventory(project))


def _time(value: object) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None and stamp.utcoffset() is not None else None


def _inventory_fresh(current: tuple[Path, dict] | None, queue: dict, now: datetime) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        return False
    if current is None:
        return False
    try:
        captured = datetime.fromisoformat(
            str(current[1]["captured_at"]).replace("Z", "+00:00")
        )
        max_age = float(queue.get("shorts_inventory_max_age_hours", 24))
        age = now.astimezone(timezone.utc) - captured.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError):
        return False
    return max_age > 0 and timedelta(0) <= age <= timedelta(hours=max_age)


def inventory_is_fresh(project: Path, queue: dict, now: datetime) -> bool:
    """Return whether the newest complete inventory is usable for selection."""

    return _inventory_fresh(_inventory(Path(project)), queue, now)


def aligned_entries(project: Path, queue: dict, now: datetime) -> list[dict]:
    if not is_shorts_aligned(queue):
        return []
    if now.tzinfo is None or now.utcoffset() is None:
        return []
    project = Path(project)
    current_inventory = _inventory(project)
    if current_inventory is None:
        return []
    if not _inventory_fresh(current_inventory, queue, now):
        return []
    ranked: list[tuple[datetime, str, dict]] = []
    for entry in queue.get("entries", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("status") == "published" or entry.get("published_url"):
            continue
        if entry.get("do_not_retry") or entry.get("status") in {"blocked", "reconcile_required"}:
            continue
        status = entry.get("status")
        if status == "pending":
            if int(entry.get("attempts") or 0) != 0:
                continue
        elif status == "failed":
            eligible = _time(entry.get("next_eligible_at"))
            if eligible is None or eligible > now:
                continue
        else:
            continue
        proof = _verified_shorts(project, entry, current_inventory)
        if proof is None:
            continue
        ranked.append((datetime.fromisoformat(proof["scheduled_at"]), str(entry["source_key"]), entry))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [entry for _, _, entry in ranked]


def _uncertain_source(project: Path, queue: dict) -> str | None:
    for entry in queue.get("entries", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("status") == "published" or entry.get("published_url"):
            continue
        if entry.get("status") == "reconcile_required":
            return str(entry.get("source_key") or "unknown")
        raw = entry.get("provider_evidence")
        if not isinstance(raw, str) or not raw:
            continue
        evidence = Path(raw)
        if not evidence.is_absolute():
            evidence = project / evidence
        markers = [evidence.with_name(name) for name in UNCERTAIN_MARKERS
                   if evidence.with_name(name).is_file()]
        if markers and not _proven_not_published(entry, markers):
            return str(entry.get("source_key") or "unknown")
    return None


def _proven_not_published(entry: dict, markers: list[Path]) -> bool:
    """Recognize the recorded deletion reconciliation without hiding ambiguity."""

    if entry.get("status") != "blocked" or entry.get("do_not_retry") is not True:
        return False
    match = re.fullmatch(
        r"source_is_not_longform: .+ articleid ([0-9]+) 삭제 완료",
        str(entry.get("last_error") or ""),
    )
    if not match:
        return False
    article_id = match.group(1)
    source_key = str(entry.get("source_key") or "")
    reservations = [_read_json(path) for path in markers
                    if path.name == "12_provider_success_reservation.json"]
    return len(reservations) == 1 and (
        reservations[0].get("sourceKey") or reservations[0].get("source_key")
    ) == source_key and str(
        reservations[0].get("articleId") or reservations[0].get("article_id") or ""
    ) == article_id


def alignment_block(
    project: Path, queue: dict, now: datetime, source_key: str | None
) -> str | None:
    """Explain why an aligned provider action must stop, else return None."""

    if not is_shorts_aligned(queue):
        return None
    uncertain = _uncertain_source(Path(project), queue)
    if uncertain:
        return f"reconcile_required:{uncertain}"
    windows = queue.get("windows")
    if not isinstance(windows, list) or not windows:
        return "alignment_windows_missing"
    try:
        hours = {int(str(value).split(":", 1)[0]) for value in windows}
    except (TypeError, ValueError):
        return "alignment_windows_invalid"
    if now.tzinfo is None or now.utcoffset() is None:
        return "alignment_now_timezone_missing"
    if now.hour not in hours:
        return f"outside_publish_window:{now.hour}"
    entries = aligned_entries(Path(project), queue, now)
    if not entries:
        return "no_shorts_aligned_entry"
    expected = str(entries[0].get("source_key") or "")
    if not source_key:
        return f"source_key_required:{expected}"
    if source_key != expected:
        return f"source_key_not_first_aligned:{source_key}!={expected}"
    return None
