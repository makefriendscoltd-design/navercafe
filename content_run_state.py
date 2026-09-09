"""Durable, provider-backed state for the daily reference producer."""

from __future__ import annotations

import json
from pathlib import Path


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def partial_artifacts(root: Path) -> list[str]:
    """Existing canonical-looking files that are unsafe to overwrite or trust."""
    problems = []
    for relative in ("shorts/production_manifest.json", "cafe/06_cafe_manifest.json",
                     "cardnews/04_cardnews_deck.json"):
        path = root / relative
        if path.is_file() and not read_json(path):
            problems.append(f"{relative}: invalid_or_empty_json")
    script = root / "shorts/07_script_final.txt"
    if script.is_file() and not script.read_text(encoding="utf-8").strip():
        problems.append("shorts/07_script_final.txt: empty")
    video = root / "shorts/final.mp4"
    if video.is_file() and video.stat().st_size < 100_000:
        problems.append("shorts/final.mp4: truncated")
    return problems


def output_roots(project: Path, source_key: str) -> list[Path]:
    return sorted(
        (p for p in (project / "outputs").glob(source_key + "-20??????") if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )


def resumable_root(project: Path, source_key: str, today: str) -> Path:
    roots = output_roots(project, source_key)
    return roots[0] if roots else project / "outputs" / f"{source_key}-{today}"


def cafe_state(project: Path, source_key: str) -> dict:
    queue = read_json(project / "outputs/cafe-publish-queue-20260823/queue.json")
    row = next((x for x in queue.get("entries", []) if x.get("source_key") == source_key), None)
    if not row:
        return {"status": "missing", "verified": False}
    verified = row.get("status") == "published" and bool(row.get("published_url"))
    return {"status": row.get("status", "unknown"), "verified": verified,
            "url": row.get("published_url"), "enrolled": True,
            "next_eligible_at": row.get("next_eligible_at") or row.get("not_before"),
            "last_error": row.get("last_error")}


def shorts_state(root: Path, source_key: str) -> dict:
    journal = read_json(root / "shorts/provider/journal.json")
    verified = journal.get("status") == "complete" and journal.get("source_key") == source_key
    proof = journal.get("verified") or {}
    checks = proof.get("checks") or {}
    verified = verified and bool(proof.get("shorts_url")) and bool(checks) and all(v is True for v in checks.values())
    return {"status": journal.get("status", "missing"), "verified": verified,
            "url": proof.get("shorts_url"), "scheduled_at": proof.get("scheduled_at")}


def community_state(root: Path, source_key: str) -> dict:
    receipt = read_json(root / "cardnews/provider/publish_receipt.json")
    verified = (receipt.get("status") == "published" and receipt.get("verified") is True
                and receipt.get("source_key") == source_key and bool(receipt.get("url")))
    if verified:
        return {"status": "published", "verified": True, "protected": False,
                "url": receipt.get("url")}
    legacy_paths = list((root / "cardnews/provider").glob("*.json"))
    legacy_paths += list((root / "community/provider").glob("*.json"))
    for path in legacy_paths:
        legacy = read_json(path)
        post = legacy.get("post") or legacy
        if legacy.get("source_key") == source_key and (post.get("post_url") or post.get("post_id")):
            return {"status": "legacy_provider_verify_only", "verified": False,
                    "protected": True, "needs_review": True, "url": post.get("post_url")}
    return {"status": receipt.get("status", "missing"), "verified": False,
            "protected": False, "url": receipt.get("url")}


def source_state(project: Path, source_key: str) -> dict:
    roots = output_roots(project, source_key)
    root = roots[0] if roots else project / "outputs" / f"{source_key}-missing"
    channels = {"cafe": cafe_state(project, source_key),
                "shorts": shorts_state(root, source_key),
                "community": community_state(root, source_key)}
    community_handed_off = channels["community"]["verified"] or channels["community"].get("protected", False)
    handed_off = (channels["cafe"].get("enrolled", False)
                  and channels["shorts"]["verified"]
                  and community_handed_off)
    visual = read_json(root / "shorts/visual_validation.json")
    terminal_blocked = (visual.get("status") == "rejected_still_source"
                        and channels["cafe"].get("enrolled", False)
                        and community_handed_off)
    complete = all(x["verified"] for x in channels.values())
    partial = partial_artifacts(root)
    return {"source_key": source_key, "root": str(root), "channels": channels,
            "handed_off": handed_off, "terminal_blocked": terminal_blocked,
            "needs_review": partial,
            "needs_production": not (handed_off or terminal_blocked or partial),
            "complete": complete}


def inventory(project: Path, source_keys) -> list[dict]:
    """One compact, deterministic row per source for reports and notifications."""
    return [source_state(project, key) for key in sorted(set(source_keys))]
