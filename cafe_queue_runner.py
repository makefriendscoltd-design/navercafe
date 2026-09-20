#!/usr/bin/env python3
"""큐에서 발행 시각이 된 카페글을 골라 실제로 발행한다.

큐(queue.json)는 발행 시각을 예약해두지만, 그 시각이 됐을 때 발행기를 부르는
주체가 tracked code에 없었다. 2026-09-12까지는 사람이 그 자리를 메웠고
2026-09-13부터 아무도 누르지 않아 발행이 0건이 됐다. 이 스크립트가 그 빈
자리다.

정책은 전부 queue.json에서 읽는다(하루 최대 성공 건수, 최소 간격, 실행당
시도 횟수, 재시도 간격). 코드에 정책 값을 복제하지 않는다.

발행은 엔트리에 박혀 있는 publisher_command를 그대로 실행한다. 이 러너는
"언제 누구를 부를지"만 정하고, 발행 자체와 그 검증은 기존 발행기가 한다.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
DEFAULT_PROJECT = Path("/Users/apple/orca/navercafe")
QUEUE_RELATIVE = Path("outputs/cafe-publish-queue-20260823/queue.json")
GATE_FIELDS = ("approval_gate", "launch_gate", "local_gate")


def parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw).astimezone(KST)


def load_queue(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def is_done(entry: dict) -> bool:
    return bool(entry.get("published_url")) or entry.get("status") == "published"


def due_entries(queue: dict, now: datetime) -> list[dict]:
    """정책상 지금 시도할 수 있는 항목을 오래된 예약 순으로 돌려준다.

    failure_policy: due pending 과 재시도 가능한 failed 를 합치고,
    not_before 가 가장 오래된 것부터 발행한다.
    """
    out = []
    for entry in queue.get("entries", []):
        if is_done(entry) or entry.get("do_not_retry"):
            continue
        status = entry.get("status")
        if status not in {"pending", "failed"}:
            continue  # blocked 는 사람이 푼다
        not_before = parse_time(entry.get("not_before"))
        if not_before is None or not_before > now:
            continue
        next_eligible = parse_time(entry.get("next_eligible_at"))
        if next_eligible and next_eligible > now:
            continue
        out.append(entry)
    out.sort(key=lambda e: str(e.get("not_before")))
    return out


def successes_today(queue: dict, now: datetime) -> list[datetime]:
    stamps = []
    for entry in queue.get("entries", []):
        if not is_done(entry):
            continue
        stamp = parse_time(entry.get("last_attempt_at"))
        if stamp and stamp.date() == now.date():
            stamps.append(stamp)
    return sorted(stamps)


def last_success(queue: dict) -> datetime | None:
    stamps = [
        parse_time(e.get("last_attempt_at"))
        for e in queue.get("entries", [])
        if is_done(e) and e.get("last_attempt_at")
    ]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def rate_block(queue: dict, now: datetime) -> str | None:
    """정책상 지금 발행하면 안 되는 이유. 없으면 None."""
    daily_max = int(queue["maximum_successes_per_day"])
    today = successes_today(queue, now)
    if len(today) >= daily_max:
        return f"오늘 이미 {len(today)}건 발행 (정책 상한 {daily_max}건)"

    gap = timedelta(hours=float(queue["minimum_gap_hours"]))
    previous = last_success(queue)
    if previous and now - previous < gap:
        remaining = gap - (now - previous)
        minutes = int(remaining.total_seconds() // 60)
        return (
            f"직전 발행({previous.isoformat()})과 최소 간격 "
            f"{queue['minimum_gap_hours']}시간 미충족 — {minutes}분 남음"
        )

    windows = {int(v.split(":", 1)[0]) for v in queue["windows"]}
    if now.hour not in windows:
        return f"현재 {now.hour}시는 발행 윈도우가 아님"
    return None


def missing_gates(project: Path, entry: dict) -> list[str]:
    return [
        field
        for field in GATE_FIELDS
        if not (project / str(entry.get(field) or "")).is_file()
    ]


def run_command(project: Path, command: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        shell=True,
        cwd=project,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def read_provider_url(project: Path, entry: dict) -> str | None:
    """발행기가 남긴 provider evidence 에서 카페 글 URL 을 찾는다."""
    relative = entry.get("provider_evidence")
    if not relative:
        return None
    path = project / str(relative)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None

    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if (
                    isinstance(value, str)
                    and "cafe.naver.com" in value
                    and ("articles/" in value or "articleid=" in value)
                ):
                    return value
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return None


def save_queue(path: Path, queue: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


# Login and daemon faults are about the machine, not the entry, so they must not
# count toward locking one out. Everything else is the entry's own failure.
ENVIRONMENT_CAUSES = ("naver login required", "로그인이 필요", "AsideLoginRequired",
                      "daemon is not reachable", "REPL context is disposed", "데몬이 죽어")
SAME_CAUSE_LIMIT = 3


def failure_cause(note: str) -> str:
    """A coarse label for why a publish failed, stable enough to compare runs."""
    text = (note or "").strip()
    for marker in ("이미지 업로드 완료를 확인하지 못했습니다",
                   "화면 캡처를 받지 못했습니다",
                   "결과 마커를 찾지 못했습니다",
                   "editor_quote_heading_not_found",
                   "too many arguments",
                   "timeout after"):
        if marker in text:
            return marker
    tail = [line for line in text.splitlines() if line.strip()]
    return (tail[-1] if tail else "unknown")[:80]


def environmental(note: str) -> bool:
    lines = [line for line in (note or "").splitlines() if line.strip()]
    raised = lines[-1] if lines else ""
    return any(marker in raised for marker in ENVIRONMENT_CAUSES)


def daemon_died(note: str) -> bool:
    """Did the run fail because the Aside daemon went away rather than the work?

    Its QuickJS runtime aborts on a GC assertion and takes the daemon down, so
    the browser was never opened and the entry is exactly as it was.
    """
    return ("daemon is not reachable" in note
            or "REPL context is disposed" in note
            or "데몬이 죽어" in note)


def record(
    project: Path, queue_path: Path, source_key: str, now: datetime, ok: bool, note: str
) -> None:
    """발행 시도 결과를 큐에 쓴다. 큐를 다시 읽어서 최신 상태 위에 기록한다."""
    queue = load_queue(queue_path)
    for entry in queue.get("entries", []):
        if entry.get("source_key") != source_key:
            continue
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        entry["last_attempt_at"] = now.isoformat()
        if ok:
            url = read_provider_url(project, entry)
            entry["status"] = "published"
            entry["published_url"] = url
            entry["next_eligible_at"] = None
            entry["result"] = {"ok": True, "note": note, "url": url}
        else:
            retry = float(queue.get("retry_interval_hours") or 1)
            cause = failure_cause(note)
            repeats = (int(entry.get("same_cause_failures") or 0) + 1
                       if entry.get("failure_cause") == cause else 1)
            entry["failure_cause"] = cause
            entry["same_cause_failures"] = repeats
            entry["result"] = {"ok": False, "note": note[:2000]}
            if repeats >= SAME_CAUSE_LIMIT and not environmental(note):
                entry["status"] = "blocked"
                entry["next_eligible_at"] = None
                entry["do_not_retry"] = True
                entry["last_error"] = f"same_cause_failures={repeats}: {cause}"
            else:
                entry["status"] = "failed"
                entry["next_eligible_at"] = (now + timedelta(hours=retry)).isoformat()
        break
    save_queue(queue_path, queue)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="무엇을 발행할지만 보여주고 아무것도 실행하지 않는다",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="발행 직전까지 자격 검증만 실행한다 (카페에 글이 올라가지 않는다)",
    )
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)

    project = args.project.resolve()
    queue_path = project / QUEUE_RELATIVE
    if not queue_path.is_file():
        print(f"큐를 찾지 못했습니다: {queue_path}", file=sys.stderr)
        return 1

    now = datetime.now(KST)
    queue = load_queue(queue_path)
    due = due_entries(queue, now)
    blocked = rate_block(queue, now)

    summary = {
        "ran_at": now.isoformat(),
        "due_count": len(due),
        "due": [e["source_key"] for e in due[:10]],
        "rate_block": blocked,
    }

    if args.dry_run:
        for entry in due:
            gates = missing_gates(project, entry)
            print(
                json.dumps(
                    {
                        "source_key": entry["source_key"],
                        "not_before": entry.get("not_before"),
                        "status": entry.get("status"),
                        "attempts": entry.get("attempts"),
                        "missing_gates": gates,
                        "command": entry.get("publisher_command"),
                    },
                    ensure_ascii=False,
                )
            )
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    if blocked:
        print(json.dumps({**summary, "action": "skip"}, ensure_ascii=False))
        return 0
    if not due:
        print(json.dumps({**summary, "action": "nothing_due"}, ensure_ascii=False))
        return 0

    attempts_per_run = int(queue.get("maximum_attempts_per_run") or 1)
    done = 0
    for entry in due:
        if done >= attempts_per_run:
            break
        source_key = entry["source_key"]
        gates = missing_gates(project, entry)
        if gates:
            print(
                json.dumps(
                    {"source_key": source_key, "action": "skip", "missing_gates": gates},
                    ensure_ascii=False,
                )
            )
            continue

        if args.validate_only:
            command = entry.get("eligibility_command")
            if not command:
                print(
                    json.dumps(
                        {"source_key": source_key, "action": "no_eligibility_command"},
                        ensure_ascii=False,
                    )
                )
                continue
            proc = run_command(project, command, args.timeout)
            print(
                json.dumps(
                    {
                        "source_key": source_key,
                        "action": "validate_only",
                        "exit": proc.returncode,
                        "tail": (proc.stdout or proc.stderr or "").strip()[-600:],
                    },
                    ensure_ascii=False,
                )
            )
            done += 1
            continue

        command = entry.get("publisher_command")
        if not command:
            print(
                json.dumps(
                    {"source_key": source_key, "action": "no_publisher_command"},
                    ensure_ascii=False,
                )
            )
            continue

        started = datetime.now(KST)
        try:
            proc = run_command(project, command, args.timeout)
            ok = proc.returncode == 0
            note = (proc.stdout or "").strip()[-2000:] if ok else (
                (proc.stderr or proc.stdout or "").strip()[-2000:]
            )
        except subprocess.TimeoutExpired:
            ok, note = False, f"timeout after {args.timeout}s"

        if not ok and daemon_died(note):
            print(json.dumps({
                "source_key": source_key, "action": "abort",
                "reason": "aside_daemon_down",
                "detail": "Aside 데몬이 죽어 브라우저를 열지 못했습니다. "
                          "이 항목은 시도로 세지 않습니다. Aside를 재시작하고 다시 실행하세요.",
            }, ensure_ascii=False), flush=True)
            summary["aborted"] = "aside_daemon_down"
            break

        record(project, queue_path, source_key, started, ok, note)
        print(
            json.dumps(
                {
                    "source_key": source_key,
                    "action": "publish",
                    "ok": ok,
                    "note": note[-600:],
                },
                ensure_ascii=False,
            )
        )
        done += 1

    print(json.dumps({**summary, "attempted": done}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    lock_path = Path("/tmp/cafe_queue_runner.lock")
    handle = lock_path.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("이미 cafe queue runner 실행 중", flush=True)
        raise SystemExit(0)  # 겹침은 정상 스킵이지 실패가 아니다
    raise SystemExit(main())
