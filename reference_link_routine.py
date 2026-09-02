#!/usr/bin/env python3
"""Scan the reference folder for YouTube links and build a local work queue.

This is intentionally read-only with respect to providers: it only reads the
reference directory and writes a local JSON/Markdown report.  Publishing is a
separate, explicit action through ``run_content_link.command``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit


URL_RE = re.compile(
    r"https?://(?:(?:www\.)?youtube\.com/(?:watch\?[^\s<>\"']+|shorts/[^\s<>\"'?]+|live/[^\s<>\"'?]+|embed/[^\s<>\"'?]+)|youtu\.be/[^\s<>\"'?]+)",
    re.IGNORECASE,
)
ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")
TEXT_SUFFIXES = {
    ".md", ".txt", ".markdown", ".json", ".yaml", ".yml", ".csv", ".html", ".htm",
}
IGNORED_DIRS = {".git", ".venv", ".venv312", "outputs", "_workspace", "__pycache__", ".content-research"}
IGNORED_FILES = {"AGENTS.md", "CLAUDE.md", "SHORTS_SPEC.md"}


def default_reference_dir() -> Path:
    configured = os.environ.get("REFERENCE_DIR", "").strip()
    candidates = ([Path(configured).expanduser()] if configured else []) + [
        Path("/Users/apple/orca/workspaces/navercafe/레퍼런스"),
        Path(__file__).resolve().parent.parent / "workspaces/navercafe/레퍼런스",
    ]
    for candidate in candidates:
        if str(candidate) and candidate.is_dir():
            return candidate.resolve()
    return Path("/Users/apple/orca/workspaces/navercafe/레퍼런스")


def clean_url(raw: str) -> str:
    raw = raw.rstrip(".,);]}>\"")
    parts = urlsplit(raw)
    host = parts.netloc.lower().removeprefix("www.")
    if host == "youtu.be":
        video_id = parts.path.strip("/").split("/")[0]
    elif host == "youtube.com":
        if parts.path == "/watch":
            video_id = parse_qs(parts.query).get("v", [""])[0]
        else:
            video_id = parts.path.strip("/").split("/")[-1]
    else:
        return ""
    if not ID_RE.fullmatch(video_id):
        return ""
    return f"https://youtu.be/{video_id}"


def source_key(url: str) -> str:
    return clean_url(url).rsplit("/", 1)[-1]


def title_hint(text: str, filename: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            candidate = line.lstrip("# ").strip()
            if candidate and "http" not in candidate:
                return candidate[:160]
    return Path(filename).stem


def iter_text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in IGNORED_DIRS or part.startswith(".venv") for part in path.relative_to(root).parts):
            continue
        if path.name in IGNORED_FILES:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            yield path, path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue


def scan(root: Path) -> list[dict]:
    found: dict[str, dict] = {}
    for path, text in iter_text_files(root):
        for line_no, line in enumerate(text.splitlines(), 1):
            for raw in URL_RE.findall(line):
                canonical = clean_url(raw)
                if not canonical:
                    continue
                key = source_key(canonical)
                item = found.setdefault(
                    key,
                    {
                        "source_key": key,
                        "canonical_url": canonical,
                        "title_hint": title_hint(text, path.name),
                        "locations": [],
                    },
                )
                location = {"file": str(path), "line": line_no}
                if location not in item["locations"]:
                    item["locations"].append(location)
    return sorted(found.values(), key=lambda item: item["source_key"])


def existing_source_keys(project_root: Path) -> set[str]:
    keys: set[str] = set()
    for path in project_root.glob("outputs/**/*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stack = [data]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for k, v in value.items():
                    if k in {"source_key", "sourceKey", "source_id", "sourceId"} and isinstance(v, str):
                        candidate = v.removeprefix("youtube:")
                        if ID_RE.fullmatch(candidate):
                            keys.add(candidate)
                    elif isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(value, list):
                stack.extend(value)
    return keys


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"links": {}}


def build_report(root: Path, project_root: Path, state_path: Path) -> dict:
    state = load_state(state_path)
    previous = state.get("links", {}) if isinstance(state, dict) else {}
    processed = existing_source_keys(project_root)
    items = scan(root)
    for item in items:
        key = item["source_key"]
        item["status"] = "already_in_outputs" if key in processed else ("seen" if key in previous else "new")
        item["duplicate_count"] = len(item["locations"])
        item["next_command"] = f'./run_content_link.command "{item["canonical_url"]}"'
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    new_state = {
        "schema_version": 1,
        "updated_at": now,
        "reference_dir": str(root),
        "links": {
            item["source_key"]: {
                "canonical_url": item["canonical_url"],
                "first_seen_at": previous.get(item["source_key"], {}).get("first_seen_at", now),
                "last_seen_at": now,
                "locations": item["locations"],
            }
            for item in items
        },
    }
    return {"schema_version": 1, "scanned_at": now, "reference_dir": str(root), "items": items, "state": new_state}


def write_outputs(report: dict, output_dir: Path, state_path: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(report["state"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "youtube_links.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 레퍼런스 YouTube 링크 큐",
        "",
        f"- 스캔 시각: `{report['scanned_at']}`",
        f"- 레퍼런스: `{report['reference_dir']}`",
        "- 이 보고서는 로컬 링크 수집만 수행하며 외부 발행을 호출하지 않습니다.",
        "",
    ]
    if not report["items"]:
        lines.append("발견된 YouTube 링크가 없습니다.")
    for index, item in enumerate(report["items"], 1):
        lines += [
            f"## {index}. {item['title_hint']}",
            "",
            f"- 상태: `{item['status']}`",
            f"- source_key: `{item['source_key']}`",
            f"- 정규 URL: {item['canonical_url']}",
            f"- 중복 발견: `{item['duplicate_count']}`곳",
            f"- 실행 명령(승인 후): `{item['next_command']}`",
            "- 발견 위치:",
        ]
        lines += [f"  - `{loc['file']}:{loc['line']}`" for loc in item["locations"]]
        lines.append("")
    (output_dir / "YOUTUBE_LINK_QUEUE.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-dir", type=Path, default=default_reference_dir())
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/reference-link-routine"))
    parser.add_argument("--state", type=Path, default=Path("outputs/reference-link-routine/state.json"))
    args = parser.parse_args(argv)
    root = args.reference_dir.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"reference directory not found: {root}")
    project_root = Path(__file__).resolve().parent
    report = build_report(root, project_root, args.state)
    write_outputs(report, args.output_dir, args.state)
    counts = {status: sum(item["status"] == status for item in report["items"]) for status in ("new", "seen", "already_in_outputs")}
    print(json.dumps({"reference_dir": str(root), "total": len(report["items"]), "counts": counts, "queue": str(args.output_dir / "YOUTUBE_LINK_QUEUE.md")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
