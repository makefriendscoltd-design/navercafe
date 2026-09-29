"""Cafe request eligibility, independent of Shorts status and publish calendars."""
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

from cafe_shorts_alignment import _uncertain_source


def is_immediate(queue: dict) -> bool:
    return queue.get("publication_mode") == "immediate_on_request"


def eligible_entries(project: Path, queue: dict, now: datetime,
                     source_key: str | None = None) -> list[dict]:
    if not is_immediate(queue) or now.tzinfo is None:
        return []
    ranked = []
    for index, entry in enumerate(queue.get("entries", [])):
        if source_key and entry.get("source_key") != source_key:
            continue
        if entry.get("published_url") or entry.get("do_not_retry"):
            continue
        status = entry.get("status")
        if status == "pending":
            if int(entry.get("attempts") or 0) != 0:
                continue
        elif status == "failed":
            try:
                retry = datetime.fromisoformat(entry["next_eligible_at"])
                if retry.tzinfo is None or retry > now:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
        else:
            continue
        # Existing queue order remains stable; a request can target its own
        # eligible source without being diverted to older queued material.
        ranked.append((index, entry))
    return [entry for _, entry in ranked]


def success_interval_deadline(project: Path, queue: dict) -> datetime | None:
    """Derive durable pacing from provider verification, never attempt start time."""
    seconds = float(queue.get("success_interval_seconds", 0))
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("invalid_success_interval_seconds")
    if not seconds:
        return None
    stamps = []
    for entry in queue.get("entries", []):
        raw = entry.get("provider_evidence")
        if not raw:
            continue
        path = Path(project) / raw
        for candidate in (path, path.with_name("12_provider_success_reservation.json")):
            if not candidate.is_file():
                continue
            receipt = json.loads(candidate.read_text(encoding="utf-8"))
            if receipt.get("status") not in {"published_verified", "provider_success_reserved"}:
                continue
            if not receipt.get("providerUrl"):
                continue
            stamp = datetime.fromisoformat(receipt["verifiedAt"])
            if stamp.tzinfo is None:
                raise ValueError("provider_verification_timestamp_requires_timezone")
            stamps.append(stamp)
    return max(stamps) + timedelta(seconds=seconds) if stamps else None


def publication_block(project: Path, queue: dict, now: datetime,
                      source_key: str | None) -> str | None:
    if not is_immediate(queue):
        return None
    uncertain = _uncertain_source(Path(project), queue)
    if uncertain:
        return f"reconcile_required:{uncertain}"
    if not source_key:
        return "missing_requested_source_key"
    if not eligible_entries(project, queue, now, source_key):
        return "requested_source_not_eligible"
    try:
        deadline = success_interval_deadline(project, queue)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return "success_interval_evidence_invalid"
    if deadline and now < deadline:
        return f"success_interval_until:{deadline.isoformat()}"
    return None
