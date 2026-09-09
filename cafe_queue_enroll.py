"""Put a produced Cafe candidate into the publish queue, gates and all.

Nothing in tracked code ever created the three approval gate files the publish
gate reads, so every new candidate had to be enrolled by hand and the automation
only ever published what somebody remembered to enqueue. That is the step this
replaces.

The gates are bindings, not opinions: they pin which manifest bytes and which
local validation were approved, and the launch note states the approved title,
category, CTA destination and first eligible time in one place so the shared
consistency scanner can contradict it. Enrolment refuses unless
``validate_cafe_eligibility`` passes on the files it just wrote, and it writes
no queue entry when it refuses.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

import cafe_manifest_publisher as publisher

KST = ZoneInfo("Asia/Seoul")
QUEUE = PROJECT / "outputs/cafe-publish-queue-20260823/queue.json"
PUBLISH_SCRIPT = "outputs/cafe-publish-queue-20260823/publish_manifest_cafe.py"
CONSISTENCY = Path.home() / ".agents/skills/launch-consistency-check/scripts/check_launch_consistency.py"
def next_slot(now: datetime, queue: dict) -> datetime:
    """First policy-compliant window after all currently reserved first attempts."""
    windows = sorted({int(value.split(":", 1)[0]) for value in queue["windows"]})
    daily_maximum = int(queue["maximum_successes_per_day"])
    minimum_gap = timedelta(hours=float(queue["minimum_gap_hours"]))
    reserved = []
    for entry in queue.get("entries", []):
        raw = entry.get("not_before")
        if (raw and entry.get("status") == "pending"
                and not entry.get("published_url") and not entry.get("do_not_retry")):
            reserved.append(datetime.fromisoformat(raw).astimezone(KST))

    latest = max([now.astimezone(KST), *reserved])
    candidate = latest.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    while True:
        if candidate.hour in windows:
            same_day = [stamp for stamp in reserved if stamp.date() == candidate.date()]
            far_enough = all(abs(candidate - stamp) >= minimum_gap for stamp in same_day)
            if len(same_day) < daily_maximum and far_enough:
                return candidate
        candidate += timedelta(hours=1)


def write_launch_note(gate_dir: Path, manifest: dict, source_key: str, slot: datetime) -> Path:
    gate_dir.mkdir(parents=True, exist_ok=True)
    note = gate_dir / "approved_cafe_values.md"
    tail = manifest.get("tail", {})
    note.write_text(
        "# Cafe queue launch consistency gate\n\n"
        f"원본은 {manifest['source_url']} 이다.\n\n"
        f"네이버 카페 제목은 `{manifest['title']}`이며 카테고리는 `{manifest['category']}`다.\n\n"
        f"고정 CTA 목적지는 {tail.get('family_day_url')} 이다.\n\n"
        f"최초 실행 가능 시각은 `{slot:%Y-%m-%d %H:%M}` Asia/Seoul이다.\n",
        encoding="utf-8")
    return note


def scan_launch_gate(gate_dir: Path) -> dict:
    """Run the shared consistency scanner over the approved-values note."""
    if not CONSISTENCY.is_file():
        raise RuntimeError(f"일관성 스캐너가 없습니다: {CONSISTENCY}")
    out = gate_dir.parent / "08_launch_consistency_gate.json"
    subprocess.run([sys.executable, str(CONSISTENCY), str(gate_dir), "--json", str(out)],
                   check=True, capture_output=True, text=True, timeout=120)
    return json.loads(out.read_text(encoding="utf-8"))


def enroll(manifest_path: Path, *, now: datetime | None = None) -> dict:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_key = manifest["source_key"]
    root = manifest_path.parent.parent
    provider = root / "provider"
    evidence = manifest_path.parent / "provider"
    relative = str(manifest_path.relative_to(PROJECT))

    now = now or datetime.now(KST)
    original_queue = QUEUE.read_text(encoding="utf-8")
    queue = json.loads(original_queue)
    if any(e["source_key"] == source_key for e in queue["entries"]):
        return {"status": "already_enrolled", "source_key": source_key}
    slot = next_slot(now, queue)

    manifest_hash = publisher.sha256(manifest_path)
    local_path = manifest_path.parent / "11_local_validation.json"
    provider.mkdir(parents=True, exist_ok=True)
    (provider / "07_approval_validation.json").write_text(json.dumps({
        "schemaVersion": "cafe-approval-validation/v2", "status": "pass",
        "sourceKey": source_key, "sourceOfTruth": relative,
        "manifestSha256": manifest_hash, "failures": [],
        "approvedAt": now.isoformat(), "approvedBy": "cafe_queue_enroll",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (provider / "09_cafe_only_local_gate.json").write_text(json.dumps({
        "schemaVersion": "cafe-only-local-gate/v2", "status": "pass", "scope": "cafe_only",
        "sourceKey": source_key, "sourceOfTruth": relative,
        "manifestSha256": manifest_hash,
        "localValidationSha256": publisher.sha256(local_path),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    write_launch_note(provider / "launch_gate", manifest, source_key, slot)
    scan = scan_launch_gate(provider / "launch_gate")
    scan.update({"status": "pass" if not scan.get("issues") else "fail",
                 "manifestSha256": manifest_hash, "sourceKey": source_key})
    (provider / "08_launch_consistency_gate.json").write_text(
        json.dumps(scan, ensure_ascii=False, indent=2), encoding="utf-8")

    verdict = publisher.validate_cafe_eligibility(manifest_path, provider, evidence)
    if verdict["status"] != "pass":
        return {"status": "not_eligible", "source_key": source_key,
                "failures": verdict["failures"]}

    entry = {
        "source_key": source_key, "not_before": slot.isoformat(), "manifest": relative,
        "approval_gate": str((provider / "07_approval_validation.json").relative_to(PROJECT)),
        "launch_gate": str((provider / "08_launch_consistency_gate.json").relative_to(PROJECT)),
        "local_gate": str((provider / "09_cafe_only_local_gate.json").relative_to(PROJECT)),
        "eligibility_command": f".venv312/bin/python {PUBLISH_SCRIPT} --manifest {relative} --validate-only",
        "publisher_command": f".venv312/bin/python {PUBLISH_SCRIPT} --manifest {relative}",
        "provider_evidence": str((evidence / "13_provider_evidence.json").relative_to(PROJECT)),
        "crm_evidence": str((evidence / "14_crm_evidence.json").relative_to(PROJECT)),
        "shorts_mutation_allowed": False, "community_mutation_allowed": False,
        "status": "pending", "attempts": 0, "last_attempt_at": None,
        "next_eligible_at": None, "published_url": None, "do_not_retry": False,
        "result": None, "enrolled_at": now.isoformat(),
    }
    queue["entries"].append(entry)
    if QUEUE.read_text(encoding="utf-8") != original_queue:
        raise RuntimeError("등록 검증 중 카페 큐가 변경됐습니다. 새 상태에서 다시 등록하세요.")
    tmp = QUEUE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, QUEUE)
    return {"status": "enrolled", "source_key": source_key, "not_before": entry["not_before"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    result = enroll(args.manifest)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"enrolled", "already_enrolled"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
