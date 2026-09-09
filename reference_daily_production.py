"""Produce the day's selected references across all three channels.

The research automation collects candidates every day and nothing consumed them.
This is the other half: take the newest candidates that fit what this channel
publishes and build them.

Rate is set by what the channels can actually publish -- the Cafe queue posts at
most two a day and Shorts hold two slots a day -- so producing more than that per
day only inflates the backlog.

Shorts are rendered and gated but not published here: the spec requires a real
human look at the eight checkpoint frames, and an unattended job cannot do that.
Each run leaves them rendered with the visual check pending.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import reference_selection as selection

PROJECT = Path(__file__).resolve().parent
PYTHON = PROJECT / ".venv312/bin/python"
KST = ZoneInfo("Asia/Seoul")
REPORT_DIR = PROJECT / "outputs/reference-daily-production"
DAILY_LIMIT = 2


def _run(args: list[str], *, timeout: int = 3600) -> tuple[bool, str]:
    completed = subprocess.run(
        [str(PYTHON), *args], cwd=PROJECT, capture_output=True, text=True, timeout=timeout)
    tail = (completed.stdout or "").strip().splitlines()[-1:] or \
           (completed.stderr or "").strip().splitlines()[-1:]
    return completed.returncode == 0, (tail[0] if tail else "")[:300]


def produce(source_key: str, today: str) -> dict:
    """Shorts render, Cafe candidate and card deck for one source."""
    steps: dict[str, str] = {}
    root = PROJECT / "outputs" / f"{source_key}-{today}"
    root.mkdir(parents=True, exist_ok=True)

    ok, note = _run(["shorts_new_prepare.py", source_key])
    steps["shorts_prepare"] = "ok" if ok else f"fail: {note}"
    if ok:
        ok, note = _run(["shorts_v7_builder.py", "--root", str(root / "shorts"), "--render"])
        steps["shorts_render"] = "ok" if ok else f"fail: {note}"

    ok, note = _run(["-c", (
        "import sys; sys.path.insert(0, '.');"
        "import youtube_cafe_auto as auto, notebooklm_source as nlm;"
        f"cfg = nlm.load_config(auto.load_or_create_config());"
        f"cfg['evidence_dir'] = r'{root / 'cafe' / 'notebooklm'}';"
        "import pathlib; pathlib.Path(cfg['evidence_dir']).mkdir(parents=True, exist_ok=True);"
        f"nlm.fetch_manuscript('https://youtu.be/{source_key}', cfg)")])
    steps["cafe_answer"] = "ok" if ok else f"fail: {note}"
    if ok:
        ok, note = _run(["cafe_new_prepare.py", source_key])
        steps["cafe_candidate"] = "ok" if ok else f"fail: {note}"
    if steps.get("cafe_candidate") == "ok":
        ok, note = _run(["content_workflow.py", "prepare-cardnews",
                         "--cafe-manifest", str(root / "cafe/06_cafe_manifest.json"),
                         "--candidate", str(root / "cardnews")])
        steps["cardnews"] = "ok" if ok else f"fail: {note}"

    return {"source_key": source_key, "root": str(root), "steps": steps,
            "complete": all(v == "ok" for v in steps.values()) and len(steps) == 5}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DAILY_LIMIT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    now = datetime.now(KST)
    today = now.strftime("%Y%m%d")
    selected, rejected = selection.select(selection.load_candidates(), limit=args.limit)
    report = {"ran_at": now.isoformat(), "limit": args.limit,
              "selected": [c["id"] for c in selected],
              "rejected_count": len(rejected), "results": []}
    print(json.dumps({"selected": [(c["id"], c["title"][:50]) for c in selected],
                      "rejected": len(rejected)}, ensure_ascii=False), flush=True)
    if args.dry_run:
        return 0

    for candidate in selected:
        try:
            report["results"].append(produce(candidate["id"], today))
        except Exception:
            report["results"].append({"source_key": candidate["id"], "steps": {},
                                      "complete": False, "error": traceback.format_exc()[-400:]})
        print(json.dumps(report["results"][-1], ensure_ascii=False), flush=True)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"{today}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    done = sum(1 for r in report["results"] if r.get("complete"))
    print(f"완료 {done}/{len(selected)} · 쇼츠는 8시점 육안검사 후 발행", flush=True)
    return 0 if done == len(selected) else 1


if __name__ == "__main__":
    raise SystemExit(main())
