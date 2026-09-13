"""Pick which discovered YouTube references are worth producing.

The daily research automation only collects candidates; nothing consumed them, so
80 piled up. What this channel actually publishes is one practitioner showing a
solo operator or small business how to do a single thing with AI -- that is the
shape both the Shorts script and the Cafe body are built around: five concrete
steps a viewer can copy. Everything selected here has to fit that.

Selection is read-only. Producing is a separate step.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
SEEN_PATH = Path.home() / ".agent-reach/navercafe-longform-seen.json"
QUEUE_PATH = PROJECT / "outputs/cafe-publish-queue-20260823/queue.json"
RUNS_PATH = PROJECT / "outputs/reference-daily-production/runs.jsonl"

# A platform vendor or a conference stage is selling infrastructure to buyers,
# not showing one person a workflow. Their talks have no five copyable steps.
VENDOR_CHANNELS = frozenset({
    "Google Cloud Tech", "Fin", "CNCF [Cloud Native Computing Foundation]",
    "Automation Anywhere", "EZLynx", "Zapier", "Microsoft", "AWS Events",
    "IBM Technology", "Salesforce",
})
# Certification factories teach a syllabus to exam takers; the audience here is
# running a business this week.
COURSE_MILL_CHANNELS = frozenset({
    "Simplilearn", "edureka!", "Learn Skills Daily", "AI Master", "AI Edge",
    "Wisdom Speaks", "Tech With Tim", "Great Learning", "Intellipaat",
})
# A weekly roundup or a ranked tool list has nothing to lift into five steps.
ROUNDUP_TITLE = re.compile(
    r"(?:Weekly|주간)\s|Updates?\s+Weekly|Best AI Tools|Tools of 20\d\d|"
    r"—\s*Ranked|- Ranked|Top \d+\s",
    re.I,
)
# A numbered podcast episode is a conversation, not a walkthrough; there is no
# ordered method in it to turn into five steps.
PODCAST_TITLE = re.compile(r"\bEp(?:isode)?\.?\s*\d+\b|\bPodcast\b", re.I)
KOREAN = re.compile(r"[가-힣]")
# Long enough to carry five distinct steps, short enough that five of them are
# the substance rather than a thin skim of a course.
MIN_MINUTES = 8
MAX_MINUTES = 120


def rejection_reason(candidate: dict) -> str | None:
    """Why this candidate is not the kind of source this channel republishes."""
    channel = str(candidate.get("channel") or "")
    title = str(candidate.get("title") or "")
    seconds = candidate.get("duration_seconds")
    minutes = candidate.get("minutes")
    if channel in VENDOR_CHANNELS:
        return "벤더·컨퍼런스 발표"
    if channel in COURSE_MILL_CHANNELS:
        return "강의 공장형 채널"
    if ROUNDUP_TITLE.search(title):
        return "주간 요약·툴 랭킹"
    if PODCAST_TITLE.search(title):
        return "팟캐스트 회차"
    if KOREAN.search(title) or KOREAN.search(channel):
        return "이미 한국어 원본"
    if seconds is None and (minutes is None or int(minutes) <= 0):
        return "영상 길이 미확인"
    seconds = int(seconds) if seconds is not None else int(minutes) * 60
    if seconds > MAX_MINUTES * 60:
        return f"{seconds // 60}분 코스 덤프"
    if seconds < MIN_MINUTES * 60:
        return f"{seconds // 60}분으로 너무 짧음"
    return None


def _date_key(value: object) -> str:
    """Normalize compact dates and ISO offsets to a UTC-sortable timestamp."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        if re.fullmatch(r"\d{8}", raw):
            parsed = datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return ""


def already_produced(source_key: str) -> bool:
    """True once production handed every channel off, even if Cafe is still queued."""
    from content_run_state import source_state
    return not source_state(PROJECT, source_key)["needs_production"]


def _last_attempts(path: Path = RUNS_PATH) -> dict[str, str]:
    attempts: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    # Include legacy JSON reports until the append-only journal has history.
    for report_path in path.parent.glob("*.json"):
        try:
            lines.append(report_path.read_text(encoding="utf-8"))
        except OSError:
            continue
    for line in lines:
        try:
            run = json.loads(line)
        except ValueError:
            continue
        at = str(run.get("ran_at") or "")
        for result in run.get("results", []):
            key = result.get("source_key")
            if key and at > attempts.get(key, ""):
                attempts[key] = at
    return attempts


def load_candidates(seen_path: Path | None = None) -> list[dict]:
    """Every discovered reference that has no production yet."""
    path = seen_path or SEEN_PATH
    seen = json.loads(path.read_text(encoding="utf-8"))
    out = []
    attempts = _last_attempts()
    for vid, meta in (seen.get("videos") or {}).items():
        if already_produced(vid):
            continue
        seconds = meta.get("duration_seconds", meta.get("duration"))
        out.append({
            "id": vid,
            "title": str(meta.get("title") or ""),
            "channel": str(meta.get("channel") or ""),
            "minutes": round(seconds / 60) if seconds else None,
            "duration_seconds": seconds,
            "upload_date": str(meta.get("upload_date") or ""),
            "first_seen": str(meta.get("seen_at") or meta.get("first_seen") or ""),
            "has_output": bool(list((PROJECT / "outputs").glob(vid + "-20??????"))),
            "last_attempt": attempts.get(vid, ""),
        })
    return out


def select(candidates: list[dict], *, limit: int | None = None) -> tuple[list[dict], list[dict]]:
    """Split candidates into what to produce and what to drop, newest first."""
    keep, drop = [], []
    for candidate in candidates:
        reason = rejection_reason(candidate)
        if reason:
            drop.append({**candidate, "reason": reason})
        else:
            keep.append(candidate)
    retries = [c for c in keep if c.get("has_output")]
    fresh = [c for c in keep if not c.get("has_output")]
    retries.sort(key=lambda c: (_date_key(c.get("last_attempt")) or "0000",
                                _date_key(c.get("first_seen"))))
    fresh.sort(key=lambda c: (_date_key(c.get("first_seen")),
                              _date_key(c.get("upload_date"))), reverse=True)
    ordered = fresh + retries
    return (ordered[:limit] if limit else ordered), drop


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    keep, drop = select(load_candidates(), limit=args.limit)
    if args.json:
        print(json.dumps({"selected": keep, "rejected": drop}, ensure_ascii=False, indent=2))
        return 0
    print(f"선별 {len(keep)}건 / 제외 {len(drop)}건")
    for c in keep:
        print(f"  {c['id']:<13}{c['minutes']:>4}분  {c['channel'][:22]:<24}{c['title'][:50]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
