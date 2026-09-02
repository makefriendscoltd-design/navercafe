#!/usr/bin/env python3
"""Discover fresh/similar YouTube references and write a daily local digest."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import reference_link_routine as links


ATOM = "http://www.w3.org/2005/Atom"


def channel_recent(channel: dict, cutoff: datetime) -> list[dict]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['channel_id']}"
    try:
        body = urllib.request.urlopen(url, timeout=15).read()
        root = ET.fromstring(body)
    except Exception:
        return []
    result = []
    for entry in root.findall(f"{{{ATOM}}}entry"):
        published = entry.findtext(f"{{{ATOM}}}published", "")
        try:
            when = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when < cutoff:
            continue
        video_id = entry.findtext("{http://www.youtube.com/xml/schemas/2015}videoId", "")
        title = entry.findtext(f"{{{ATOM}}}title", "").strip()
        if video_id:
            result.append({"kind": "same_channel", "channel": channel["name"], "title": title, "video_id": video_id, "published": when.isoformat()})
    return result


def topic_search(query: str, limit: int) -> list[dict]:
    cmd = [".venv312/bin/yt-dlp", "--flat-playlist", "--dump-single-json", f"ytsearch{limit}:{query}"]
    try:
        raw = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60).stdout
        data = json.loads(raw)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    result = []
    for entry in data.get("entries", []):
        if not entry.get("id"):
            continue
        result.append({"kind": "similar_topic", "query": query, "channel": entry.get("channel") or entry.get("uploader") or "", "title": entry.get("title") or "", "video_id": entry["id"]})
    return result


def discover(config: dict, days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    candidates = []
    for channel in config.get("channels", []):
        candidates.extend(channel_recent(channel, cutoff)[: int(config.get("per_channel", 2))])
    for query in config.get("queries", []):
        candidates.extend(topic_search(query, int(config.get("per_query", 2))))
    existing = {item["source_key"] for item in links.scan(links.default_reference_dir())}
    deduped = []
    seen = set(existing)
    for item in candidates:
        key = item["video_id"]
        if key in seen or not re.fullmatch(r"[A-Za-z0-9_-]{6,}", key):
            continue
        seen.add(key)
        item["url"] = f"https://youtu.be/{key}"
        deduped.append(item)
    return deduped[: int(config.get("max_candidates", 20))]


def write_digest(candidates: list[dict], reference_dir: Path, now: datetime) -> Path:
    out_dir = reference_dir / "발견"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now.astimezone().date().isoformat()}.md"
    lines = ["# 오늘의 YouTube 레퍼런스 후보", "", f"발견 시각: `{now.astimezone().isoformat(timespec='seconds')}`", "", "> 후보 수집만 수행했습니다. 제작·발행은 링크를 검토한 뒤 별도로 실행합니다.", ""]
    if not candidates:
        lines.append("오늘 새 후보가 없습니다.")
    for i, item in enumerate(candidates, 1):
        label = "같은 채널 새 업로드" if item["kind"] == "same_channel" else f"유사 주제: {item['query']}"
        lines += [f"## {i}. {item['title'] or '(제목 미확인)'}", "", f"- 분류: `{label}`", f"- 채널: `{item.get('channel', '')}`", f"- 링크: {item['url']}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("reference_discovery_config.json"))
    parser.add_argument("--reference-dir", type=Path, default=links.default_reference_dir())
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    candidates = discover(config, max(1, args.days))
    digest = None if args.no_write else write_digest(candidates, args.reference_dir.expanduser().resolve(), datetime.now().astimezone())
    print(json.dumps({"candidate_count": len(candidates), "digest": str(digest) if digest else None, "candidates": candidates}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
