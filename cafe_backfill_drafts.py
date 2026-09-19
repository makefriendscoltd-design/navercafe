#!/usr/bin/env python3
"""노트북이 꽉 차서 카페 원고 없이 지나간 원본들의 원고를 뒤늦게 만든다.

2026-09-15 에 NotebookLM 카페 노트북이 소스 50개 한도에 걸리면서 24건이
'shorts-only' 로 처리됐다. 쇼츠는 이미 만들어져 있으므로 이 스크립트는
카페 쪽만 채운다: NotebookLM 원고 -> 카페 매니페스트 -> 발행 큐 등록.

쇼츠와 커뮤니티는 건드리지 않는다. 이미 발행됐거나 예약된 것을 다시
건드리면 중복 발행이 되기 때문에, publish() 경로를 통째로 피하고 카페
준비 단계만 직접 부른다.

노트북 자리는 매 건 확인한다. 파이프라인은 소스를 추가해 쓰고 지우지만
그 정리가 실패하면 칸이 잠식되고, 그게 애초에 24건을 만든 원인이다.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path("/Users/apple/orca/navercafe")
PYTHON = PROJECT / ".venv312/bin/python"
KST = timezone(timedelta(hours=9))
NOTEBOOK_LIMIT = 50

# 발행 러너와 같은 락이다. 둘 다 Aside 브라우저의 탭을 조작하므로 동시에
# 돌면 서로의 탭을 건드린다. 백필은 이 락을 건별로만 잡는다 -- 통째로
# 쥐고 있으면 매시 발행 잡이 백필이 끝날 때까지 계속 스킵된다.
ASIDE_LOCK = Path("/tmp/cafe_queue_runner.lock")


@contextlib.contextmanager
def aside_lock(timeout: int = 1800):
    handle = ASIDE_LOCK.open("a+")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                raise TimeoutError("Aside 락을 얻지 못했습니다 (발행 잡이 오래 점유 중)")
            time.sleep(5)
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def run(args: list[str], timeout: int) -> tuple[bool, str]:
    proc = subprocess.run(
        [str(PYTHON), *args],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    ok = proc.returncode == 0
    note = (proc.stdout if ok else (proc.stderr or proc.stdout)).strip()
    return ok, note[-1500:]


def notebook_source_count() -> int | None:
    """카페 노트북의 현재 소스 수. 읽기 전용."""
    code = (
        "import json,sys; sys.path.insert(0,'.');\n"
        "from aside_browser import JS_COMMON,_payload_expression,run_repl\n"
        "p={'notebookId':'c09a56d4-b87c-4f54-bfdb-93219326fbae'}\n"
        "c=JS_COMMON+'\\nconst payload = '+_payload_expression(p)+';\\n'+r'''\n"
        "const p2=await openTab(`https://notebooklm.google.com/notebook/${payload.notebookId}"
        "?authuser=1&aside_pipeline=${Date.now()}`);\n"
        "await sleep(6000);\n"
        "for(let i=0;i<20;i++){const n=await p2.evaluate(()=>document.querySelectorAll"
        "('input[type=\"checkbox\"]').length);if(n>1)break;await sleep(800);}\n"
        "emit({count:(await p2.evaluate(()=>[...document.querySelectorAll('input[type=\"checkbox\"]')]\n"
        " .filter(x=>(x.getAttribute('aria-label')||'')!=='모든 출처 선택').length))});\n"
        "await p2.close();\n"
        "'''\n"
        "print(json.dumps(run_repl(c,timeout=120)))\n"
    )
    try:
        proc = subprocess.run(
            [str(PYTHON), "-c", code], cwd=PROJECT, capture_output=True, text=True, timeout=200
        )
        for line in reversed((proc.stdout or "").strip().splitlines()):
            try:
                return int(json.loads(line).get("count"))
            except (ValueError, TypeError, AttributeError):
                continue
    except subprocess.TimeoutExpired:
        return None
    return None


def make_manuscript(source_key: str, root: Path, timeout: int) -> tuple[bool, str]:
    """NotebookLM 카페 노트북으로 원고를 받는다 (기존 파이프라인과 동일 경로)."""
    answer = root / "cafe/notebooklm/notebooklm-answer.md"
    if answer.is_file():
        return True, "기존 NotebookLM 응답 재사용"
    evidence = root / "cafe" / "notebooklm"
    return run(
        [
            "-c",
            "import sys; sys.path.insert(0, '.');"
            "import youtube_cafe_auto as auto, notebooklm_source as nlm;"
            "cfg = nlm.load_config(auto.load_or_create_config());"
            f"cfg['evidence_dir'] = r'{evidence}';"
            "import pathlib; pathlib.Path(cfg['evidence_dir']).mkdir(parents=True, exist_ok=True);"
            f"nlm.fetch_manuscript('https://youtu.be/{source_key}', cfg)",
        ],
        timeout,
    )


def process(source_key: str, timeout: int) -> dict:
    roots = sorted(PROJECT.glob(f"outputs/{source_key}-*"))
    if not roots:
        return {"source_key": source_key, "ok": False, "step": "root", "note": "출력 디렉토리 없음"}
    root = roots[-1]
    steps: dict[str, str] = {}

    ok, note = make_manuscript(source_key, root, timeout)
    steps["cafe_answer"] = "ok" if ok else f"fail: {note}"
    if not ok:
        return {"source_key": source_key, "root": root.name, "ok": False, "steps": steps}

    if (root / "cafe/06_cafe_manifest.json").is_file():
        steps["cafe_candidate"] = "ok (기존)"
    else:
        ok, note = run(["cafe_new_prepare.py", "--", source_key], timeout)
        steps["cafe_candidate"] = "ok" if ok else f"fail: {note}"
        if not ok:
            return {"source_key": source_key, "root": root.name, "ok": False, "steps": steps}

    manifest = root / "cafe/06_cafe_manifest.json"
    ok, note = run(["cafe_queue_enroll.py", "--manifest", str(manifest)], timeout)
    steps["cafe_enroll"] = "ok" if ok else f"fail: {note}"
    return {"source_key": source_key, "root": root.name, "ok": ok, "steps": steps}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keys-file", type=Path, default=Path("/tmp/nb24.json"))
    parser.add_argument("--limit", type=int, default=0, help="0 이면 전부")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path,
                        default=PROJECT / "outputs/reference-daily-production/backfill.jsonl")
    args = parser.parse_args(argv)

    keys = json.loads(args.keys_file.read_text(encoding="utf-8"))
    if args.limit:
        keys = keys[: args.limit]

    if args.dry_run:
        for key in keys:
            roots = sorted(PROJECT.glob(f"outputs/{key}-*"))
            root = roots[-1] if roots else None
            print(json.dumps({
                "source_key": key,
                "root": root.name if root else None,
                "has_answer": bool(root and (root / "cafe/notebooklm/notebooklm-answer.md").is_file()),
                "has_manifest": bool(root and (root / "cafe/06_cafe_manifest.json").is_file()),
            }, ensure_ascii=False))
        print(json.dumps({"count": len(keys)}, ensure_ascii=False))
        return 0

    count = notebook_source_count()
    print(json.dumps({"notebook_sources": count, "limit": NOTEBOOK_LIMIT}, ensure_ascii=False),
          flush=True)
    if count is not None and count >= NOTEBOOK_LIMIT:
        print("노트북이 꽉 찼습니다. 소스를 비우기 전에는 원고를 만들 수 없습니다.", file=sys.stderr)
        return 1

    done = 0
    for key in keys:
        started = datetime.now(KST)
        try:
            with aside_lock():
                result = process(key, args.timeout)
        except subprocess.TimeoutExpired:
            result = {"source_key": key, "ok": False, "steps": {"timeout": f"{args.timeout}s"}}
        except TimeoutError as error:
            result = {"source_key": key, "ok": False, "steps": {"lock": str(error)}}
        result["ran_at"] = started.isoformat()
        with args.report.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        print(json.dumps(result, ensure_ascii=False), flush=True)
        done += 1

        # 원고를 만들 때마다 노트북 자리가 제자리로 돌아오는지 본다.
        # 늘어나고 있으면 파이프라인의 소스 정리가 실패하는 중이다.
        if done % 5 == 0:
            current = notebook_source_count()
            print(json.dumps({"checkpoint": done, "notebook_sources": current},
                             ensure_ascii=False), flush=True)
            if current is not None and current >= NOTEBOOK_LIMIT:
                print("노트북이 다시 찼습니다. 남은 건은 중단합니다.", file=sys.stderr)
                break

    print(json.dumps({"processed": done, "total": len(keys)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
