"""Give a blocked Cafe entry the NotebookLM provenance its gate now requires.

Since the provenance rule landed, an entry whose Cafe answer was never stored
alongside its provider evidence can no longer publish, and the queue has been
sitting on those. Fetch the answer from the pinned 민수대표님_카페글 notebook,
rebuild the body from it through the canonical formatter, regenerate the entry's
gates against the new manifest, and hand it back to the queue as pending.

The previous body and manifest are kept beside the new ones. Nothing here
publishes; the hourly automation stays the only thing that posts.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parent
QUEUE_DIR = PROJECT / "outputs/cafe-publish-queue-20260823"
QUEUE_PATH = QUEUE_DIR / "queue.json"
CONTRACT_PATH = QUEUE_DIR / "onboard_20260902_cafe_entries.py"
SUFFIX = "pre-provenance-repair"
KST = ZoneInfo("Asia/Seoul")


def load_contract():
    spec = importlib.util.spec_from_file_location("approved_cafe_onboarding_contract", CONTRACT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("승인된 Cafe onboarding 계약을 불러올 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch_cafe_answer(source_key: str, evidence_dir: Path) -> None:
    """Ask the pinned Cafe notebook; it writes the answer and its evidence itself."""
    import youtube_cafe_auto as auto
    import notebooklm_source as nlm

    evidence_dir.mkdir(parents=True, exist_ok=True)
    config = auto.load_or_create_config()
    cfg = nlm.load_config(config)
    cfg["evidence_dir"] = str(evidence_dir)
    nlm.fetch_manuscript(f"https://youtu.be/{source_key}", cfg)


def repair(source_key: str, *, fetch: bool = True) -> dict:
    contract = load_contract()
    queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    entry = next((e for e in queue["entries"] if e["source_key"] == source_key), None)
    if entry is None:
        raise RuntimeError(f"{source_key}는 카페 큐에 없습니다.")
    if entry.get("published_url"):
        raise RuntimeError(f"{source_key}는 이미 발행됐습니다. 손대지 않습니다.")
    manifest_path = PROJECT / entry["manifest"]
    cafe = manifest_path.parent
    root_date = re.search(r"-(\d{8})/", entry["manifest"])
    if not root_date:
        raise RuntimeError("산출물 루트 날짜를 읽지 못했습니다: " + entry["manifest"])

    if fetch:
        fetch_cafe_answer(source_key, cafe / "notebooklm")
    answer = cafe / "notebooklm/notebooklm-answer.md"
    evidence = cafe / "notebooklm/notebooklm-provider-evidence.json"
    for path in (answer, evidence):
        if not path.is_file():
            raise RuntimeError(f"카페 NotebookLM 증거가 없습니다: {path}")

    with tempfile.TemporaryDirectory() as work:
        candidate = Path(work) / "rebuilt"
        completed = subprocess.run(
            [sys.executable, str(PROJECT / "content_workflow.py"), "prepare-cafe",
             "--manifest", str(manifest_path), "--candidate", str(candidate)],
            cwd=PROJECT, capture_output=True, text=True,
        )
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout).strip().splitlines()[-4:]
            raise RuntimeError("본문 재생성 실패: " + " | ".join(tail))
        rebuilt = json.loads((candidate / "06_cafe_manifest.json").read_text(encoding="utf-8"))
        body = (candidate / "03_cafe_body.txt").read_text(encoding="utf-8")
        local = (candidate / "11_local_validation.json").read_text(encoding="utf-8")

    # Keep what was there before beside the new files rather than over them.
    for name in ("03_cafe_body.txt", "06_cafe_manifest.json", "11_local_validation.json"):
        current = cafe / name
        if current.is_file():
            backup = cafe / f"{current.stem}.{SUFFIX}{current.suffix}"
            if not backup.exists():
                shutil.copy2(current, backup)

    # The rebuilt body's own section titles describe the new sections; the old
    # image labels described the old ones.
    headings = [line[3:].strip() for line in body.splitlines() if line.startswith("## ")]
    if len(headings) == len(rebuilt.get("images", [])):
        rebuilt["image_labels"] = headings
    # Manifests written before the long-URL field exists still have to satisfy the
    # gate that now requires it. It is derived exactly from the source key.
    long_url = f"https://www.youtube.com/watch?v={source_key}"
    rebuilt.setdefault("source_long_url", None)
    if not rebuilt.get("source_long_url"):
        rebuilt["source_long_url"] = long_url
    tail = rebuilt.setdefault("tail", {})
    if not tail.get("source_long_url"):
        tail["source_long_url"] = long_url
    rebuilt["body_file"] = "03_cafe_body.txt"
    (cafe / "03_cafe_body.txt").write_text(body, encoding="utf-8")
    (cafe / "11_local_validation.json").write_text(local, encoding="utf-8")
    (cafe / "06_cafe_manifest.json").write_text(
        json.dumps(rebuilt, ensure_ascii=False, indent=2), encoding="utf-8")

    fresh, _evidence = contract.build_entry(source_key, entry["not_before"], root_date.group(1))
    for field in ("manifest", "approval_gate", "launch_gate", "local_gate",
                  "eligibility_command", "publisher_command",
                  "provider_evidence", "crm_evidence"):
        entry[field] = fresh[field]
    entry["status"] = "pending"
    entry["result"] = None
    entry["attempts"] = 0
    # The lock is what actually keeps the queue guard from ever selecting this
    # entry again. Clearing the status without it leaves the item looking ready
    # while nothing can ever pick it up.
    entry["do_not_retry"] = False
    entry["next_eligible_at"] = None
    entry["provenance_repaired_at"] = datetime.now(KST).strftime("%Y-%m-%d")
    entry.pop("blocked_at", None)
    entry.pop("blocked_reason", None)
    entry.pop("prior_status", None)
    QUEUE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "repaired", "source_key": source_key,
            "title": rebuilt["title"], "not_before": entry["not_before"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_key")
    parser.add_argument("--no-fetch", action="store_true",
                        help="이미 받아둔 원고/증거를 그대로 쓴다")
    args = parser.parse_args(argv)
    print(json.dumps(repair(args.source_key, fetch=not args.no_fetch), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
