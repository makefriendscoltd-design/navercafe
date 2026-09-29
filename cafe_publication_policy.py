"""Cafe request eligibility, independent of Shorts status and publish calendars."""
from datetime import datetime
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
    return None
