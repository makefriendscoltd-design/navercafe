"""Produce validated Shorts until today's target is met.

This runner deliberately has no Cafe, Community, browser, or provider step.  A
success means that ``final.mp4`` and its canonical machine/visual/lineage bundle
pass the same upload gates used by the Shorts publisher.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import traceback
from zoneinfo import ZoneInfo

import reference_selection as selection

PROJECT = Path(__file__).resolve().parent
PYTHON = PROJECT / ".venv312/bin/python"
KST = ZoneInfo("Asia/Seoul")
REPORT_DIR = PROJECT / "outputs/shorts-daily-production"
SHARED_LOCK = PROJECT / "outputs/reference-daily-production/run.lock"
DEFAULT_TARGET = 20
DEFAULT_WORKERS = 3
TRANSIENT_COOLDOWN = timedelta(hours=2)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def validation_problems(root: Path, *, candidate_name: str = "shorts") -> list[str]:
    """Return every reason this root cannot be counted as a finished Short."""
    import content_acceptance
    from content_production_policy import validate_shorts_render_bundle

    shorts = root / candidate_name
    problems = list(content_acceptance.check_shorts(root) if candidate_name == "shorts" else
                    content_acceptance.check_shorts(root, candidate_name=candidate_name))
    review = shorts / "editorial_review.json"
    if candidate_name != "shorts" and not review.is_file():
        problems.append("수정 후보의 원문 편집 검토 증거 없음")
    if review.exists():
        evidence = _read(review)
        script = shorts / "07_script_final.txt"
        if not evidence:
            problems.append("편집 검토 증거가 비었거나 손상됨")
        elif evidence.get("status") != "pass":
            problems.append(f"편집 검토 status={evidence.get('status') or 'missing'}")
        elif not script.is_file():
            problems.append("편집 검토 대상 대본 없음")
        else:
            digest = hashlib.sha256(script.read_bytes()).hexdigest()
            bound = evidence.get("script_sha256") or evidence.get("scriptSha256")
            if bound != digest:
                problems.append("편집 검토가 현재 대본 해시에 결속되지 않음")
            transcript = shorts / "captions/transcript.txt"
            transcript_bound = evidence.get("transcript_sha256") or evidence.get("transcriptSha256")
            if not transcript.is_file():
                problems.append("편집 검토의 로컬 전사문 없음")
            elif transcript_bound != hashlib.sha256(transcript.read_bytes()).hexdigest():
                problems.append("편집 검토가 현재 전사문 해시에 결속되지 않음")
            if evidence.get("headcopy_sha256"):
                headcopy = shorts / "06_headcopy_candidates.txt"
                if (not headcopy.is_file() or evidence["headcopy_sha256"] !=
                        hashlib.sha256(headcopy.read_bytes()).hexdigest()):
                    problems.append("편집 검토가 현재 헤드카피 해시에 결속되지 않음")
    final = shorts / "final.mp4"
    if not problems:
        try:
            validate_shorts_render_bundle(final)
        except Exception as exc:  # canonical gate owns the exact exception types
            problems.append(f"업로드 번들 검증 실패: {str(exc)[:200]}")
    return problems


def validated_shorts_root(root: Path, *, candidate_name: str = "shorts") -> bool:
    return not validation_problems(Path(root), candidate_name=candidate_name)


def prepared_shorts_root(root: Path) -> bool:
    """A current, source-bound script can go straight to the renderer."""
    shorts = Path(root) / "shorts"
    if not (shorts / "07_script_final.txt").is_file() or not (shorts / "production_manifest.json").is_file():
        return False
    try:
        from content_lineage import validate_shorts_origin
        validate_shorts_origin(shorts)
        return True
    except Exception:
        return False


def validated_today(today: str, project: Path = PROJECT) -> set[str]:
    keys: set[str] = set()
    for final in project.glob("outputs/*/shorts*/final.mp4"):
        made = datetime.fromtimestamp(final.stat().st_mtime, KST).strftime("%Y-%m-%d")
        root = final.parent.parent
        name = final.parent.name
        if name != "shorts" and not name.startswith("shorts-review-"):
            continue
        valid = (validated_shorts_root(root) if name == "shorts" else
                 validated_shorts_root(root, candidate_name=name)) if made == today else False
        if valid:
            keys.add(root.name.rsplit("-", 1)[0])
    return keys


def _run(args: list[str], timeout: int = 3600) -> tuple[bool, str]:
    try:
        done = subprocess.run([str(PYTHON), *args], cwd=PROJECT, capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)[:400]
    output = done.stdout if done.returncode == 0 else done.stderr or done.stdout
    return done.returncode == 0, "\n".join((output or "").splitlines()[-3:])[:600]


def produce_one(source_key: str, today: str) -> dict:
    """Prepare and render one source; never publish it or touch another channel."""
    import content_run_state

    root = content_run_state.resumable_root(PROJECT, source_key, today)
    root.mkdir(parents=True, exist_ok=True)
    result = {"source_key": source_key, "root": str(root), "status": "failed", "steps": {}}
    try:
        existing = root / "shorts/final.mp4"
        if existing.is_file():
            if validated_shorts_root(root):
                result["status"] = "existing_valid"
                result["problems"] = ["검증된 기존 렌더이며 오늘 새로 만든 산출물이 아님"]
            else:
                result["status"] = "existing_invalid"
                result["problems"] = validation_problems(root)
            return result
        if prepared_shorts_root(root):
            result["steps"]["prepare"] = "reuse: current source-bound inputs"
        else:
            ok, note = _run(["shorts_new_prepare.py", "--", source_key])
            result["steps"]["prepare"] = "ok" if ok else f"fail: {note}"
            if not ok:
                result["problems"] = [note]
                return result
        import shorts_editorial_review
        editorial = shorts_editorial_review.review(root / "shorts")
        result["steps"]["editorial_review"] = editorial.get("status", "missing")
        if editorial.get("status") != "pass":
            result["problems"] = [f"편집 검토 status={editorial.get('status') or 'missing'}",
                                  *[f"근거 불충분: {name}"
                                    for name in editorial.get("failed_sections") or []]]
            return result
        ok, note = _run(["shorts_v7_builder.py", "--root", str(root / "shorts"), "--render"])
        result["steps"]["render"] = "ok" if ok else f"fail: {note}"
        problems = validation_problems(root)
        result["problems"] = problems
        if ok and not problems:
            result["status"] = "pass"
        return result
    except Exception:
        result["problems"] = [traceback.format_exc()[-600:]]
        return result


def append_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False) + "\n")
        stream.flush()


def failure_disposition(result: dict, now: datetime) -> dict:
    """Keep terminal media failures out; retry environmental failures later."""
    problems = " ".join(str(x) for x in result.get("problems") or [])
    if result.get("status") == "existing_valid":
        return {"kind": "permanent", "reason": "render_already_complete_before_today"}
    if result.get("status") == "existing_invalid" or "원본이 거의 정지" in problems:
        return {"kind": "permanent", "reason": "source_or_existing_render_rejected"}
    return {"kind": "transient", "reason": "retry_after_cooldown",
            "retry_after": (now + TRANSIENT_COOLDOWN).isoformat()}


def active_failure_keys(now: datetime, report_dir: Path = REPORT_DIR) -> set[str]:
    """Read latest durable disposition per source without treating outages as permanent."""
    latest: dict[str, dict] = {}
    for path in sorted(report_dir.glob("*.jsonl")) if report_dir.is_dir() else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            key = row.get("source_key")
            if key:
                latest[key] = row.get("disposition") or {}
    blocked = set()
    for key, disposition in latest.items():
        if disposition.get("kind") == "permanent":
            blocked.add(key)
        elif disposition.get("kind") == "transient":
            try:
                retry_after = datetime.fromisoformat(disposition["retry_after"])
            except (KeyError, ValueError, TypeError):
                continue
            if retry_after > now:
                blocked.add(key)
    return blocked


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.target < 1 or args.workers < 1:
        parser.error("--target과 --workers는 1 이상이어야 합니다")

    today = datetime.now(KST).strftime("%Y-%m-%d")
    already = validated_today(today)
    needed = max(0, args.target - len(already))
    candidates, rejected = selection.select(selection.load_candidates(), limit=None)
    cooldown = active_failure_keys(datetime.now(KST))
    unavailable = already | cooldown
    candidates = [row for row in candidates if row["id"] not in unavailable]
    plan = {"status": "planned", "target": args.target, "validated_today": len(already),
            "needed": needed, "candidate_count": len(candidates), "rejected_count": len(rejected)}
    print(json.dumps(plan, ensure_ascii=False), flush=True)
    if args.dry_run or not needed:
        return 0

    SHARED_LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock = SHARED_LOCK.open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({"status": "busy", "reason": "기존 제작 실행 중"}, ensure_ascii=False))
        return 2

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(KST).strftime("%Y%m%dT%H%M%S.%f%z")
    journal = REPORT_DIR / f"{run_id}.jsonl"
    append_result(journal, {"run": {"status": "started", "run_id": run_id,
                                     "target": args.target,
                                     "validated_at_start": sorted(already),
                                     "selected_source_keys": [row["id"] for row in candidates]}})
    attempted: set[str] = set()
    successes = set(already)
    cursor = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}

        def refill() -> None:
            nonlocal cursor
            # Never launch more work than can still contribute to the target.
            while (cursor < len(candidates) and len(futures) < args.workers
                   and len(successes) + len(futures) < args.target):
                candidate = candidates[cursor]
                cursor += 1
                if candidate["id"] in attempted:
                    continue
                attempted.add(candidate["id"])
                future = pool.submit(produce_one, candidate["id"], today.replace("-", ""))
                futures[future] = candidate

        refill()
        while futures:
            completed, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in completed:
                futures.pop(future)
                result = future.result()
                recorded_at = datetime.now(KST)
                result["recorded_at"] = recorded_at.isoformat()
                if result["status"] != "pass":
                    result["disposition"] = failure_disposition(result, recorded_at)
                append_result(journal, result)
                if result["status"] == "pass":
                    successes.add(result["source_key"])
                print(json.dumps(result, ensure_ascii=False), flush=True)
            refill()

    summary = {"status": "complete" if len(successes) >= args.target else "exhausted",
               "target": args.target, "validated_today": len(successes),
               "attempted": len(attempted), "journal": str(journal)}
    append_result(journal, {"summary": summary})
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if len(successes) >= args.target else 1


if __name__ == "__main__":
    raise SystemExit(main())
