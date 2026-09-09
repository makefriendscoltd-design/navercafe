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
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
SEEN_PATH = Path.home() / ".agent-reach/navercafe-longform-seen.json"
QUEUE_PATH = PROJECT / "outputs/cafe-publish-queue-20260823/queue.json"

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
    minutes = int(candidate.get("minutes") or 0)
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
    if minutes and minutes > MAX_MINUTES:
        return f"{minutes}분 코스 덤프"
    if minutes and minutes < MIN_MINUTES:
        return f"{minutes}분으로 너무 짧음"
    return None


def already_produced(source_key: str) -> bool:
    """True when this source already has an output root or a Cafe queue entry."""
    if any(p.is_dir() for p in (PROJECT / "outputs").glob(source_key + "-*")):
        return True
    if QUEUE_PATH.is_file():
        queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        if any(e["source_key"] == source_key for e in queue["entries"]):
            return True
    return False


def load_candidates(seen_path: Path | None = None) -> list[dict]:
    """Every discovered reference that has no production yet."""
    path = seen_path or SEEN_PATH
    seen = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for vid, meta in (seen.get("videos") or {}).items():
        if already_produced(vid):
            continue
        out.append({
            "id": vid,
            "title": str(meta.get("title") or ""),
            "channel": str(meta.get("channel") or ""),
            "minutes": round((meta.get("duration") or 0) / 60),
            "upload_date": str(meta.get("upload_date") or ""),
            "first_seen": str(meta.get("first_seen") or ""),
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
    keep.sort(key=lambda c: (c.get("first_seen") or "", c.get("upload_date") or ""), reverse=True)
    return (keep[:limit] if limit else keep), drop


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
