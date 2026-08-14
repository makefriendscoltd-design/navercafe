"""
Route one YouTube link to the existing Orca worker sessions.

Usage:
    python orca_youtube_router.py "https://www.youtube.com/watch?v=..."
    python orca_youtube_router.py "https://youtu.be/..." --dry-run

This intentionally re-resolves terminal handles on every run because Orca terminal
handles can change after restarts.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


YOUTUBE_RE = re.compile(
    r"^https?://(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)"
)


@dataclass(frozen=True)
class WorkerTarget:
    key: str
    label: str
    worktree_hint: str
    branch_hint: str | None
    title_hint: str | None
    prompt: str


def run_orca(args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        ["orca", *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"orca {' '.join(args)} failed with code {proc.returncode}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"orca returned non-JSON output:\n{proc.stdout}") from exc


def list_terminals() -> list[dict[str, Any]]:
    payload = run_orca(["terminal", "list", "--json"])
    if not payload.get("ok"):
        raise RuntimeError(f"orca terminal list failed: {payload}")
    return payload["result"]["terminals"]


def score_terminal(term: dict[str, Any], target: WorkerTarget) -> int:
    path = (term.get("worktreePath") or "").replace("\\", "/").lower()
    branch = (term.get("branch") or "").lower()
    title = (term.get("title") or "").lower()
    preview = (term.get("preview") or "").lower()

    score = 0
    if target.worktree_hint.lower() in path:
        score += 100
    if target.branch_hint and target.branch_hint.lower() in branch:
        score += 40
    if target.title_hint and target.title_hint.lower() in title:
        score += 20
    if target.title_hint and target.title_hint.lower() in preview:
        score += 5
    if term.get("connected"):
        score += 5
    if term.get("writable"):
        score += 5
    return score


def pick_terminal(terminals: list[dict[str, Any]], target: WorkerTarget) -> dict[str, Any]:
    ranked = sorted(
        terminals,
        key=lambda t: (score_terminal(t, target), t.get("lastOutputAt") or 0),
        reverse=True,
    )
    if not ranked or score_terminal(ranked[0], target) < 100:
        raise RuntimeError(f"Could not find worker terminal for {target.label}")
    return ranked[0]


def send_prompt(handle: str, prompt: str) -> dict[str, Any]:
    return run_orca(
        [
            "terminal",
            "send",
            "--terminal",
            handle,
            "--text",
            prompt,
            "--enter",
            "--json",
        ]
    )


def build_targets(url: str) -> list[WorkerTarget]:
    cafe_prompt = f"""YouTube link received for the cafe publishing worker:
{url}

Use the existing YouTube link -> Naver cafe publishing pipeline in this session.
Goal: create a Naver cafe promotional/column post from this video.

Rules:
- Use the current project code and session context.
- If publication requires final confirmation, stop at the review/ready state and report what is ready.
- If this session already has a safe manual-review flow, follow that flow.
- Do not change unrelated files.
- When done, summarize: draft/generated status, cafe posting status, any blocker.
"""

    youtube_prompt = f"""YouTube link received for the NotebookLM/cardnews YouTube worker:
{url}

Use the existing NotebookLM -> cardnews -> YouTube community workflow in this session.
Goal: generate the source writeup, create the 1:1 cardnews images, fill the YouTube community draft, attach images, and stop before final public posting.

Rules:
- Use the current project code and session context.
- Keep the YouTube tab in review/handoff state if a draft is opened.
- Do not press the final public post button unless explicitly asked later.
- Do not change unrelated files.
- When done, summarize: NotebookLM status, cardnews status, YouTube draft/attachment status, any blocker.
"""

    script_prompt = f"""YouTube link received for the script/body worker:
{url}

Use this session as the script and source-body extraction worker.
Goal: extract or generate the reusable content package from this video, separate from any posting action.

Required outputs:
- video/source summary
- clean transcript or script, if available
- reusable long-form body/manuscript
- key points and suggested title candidates
- save the outputs to local files when the current project has an established output convention

Rules:
- Do not publish anywhere.
- Do not operate browser posting surfaces.
- Do not change unrelated files.
- When done, summarize: extracted/generated files, transcript status, body/script status, any blocker.
"""

    return [
        WorkerTarget(
            key="cafe",
            label="Naver cafe worker",
            worktree_hint="D:/coding/ccidacafe",
            branch_hint="feat/notebooklm-cafe-publisher",
            title_hint="카페 블로그 자동 작성",
            prompt=cafe_prompt,
        ),
        WorkerTarget(
            key="youtube",
            label="NotebookLM YouTube worker",
            worktree_hint="/ccidacafe/noteboolm_youtube",
            branch_hint="noteboolm_youtube",
            title_hint="noteboolm_youtube",
            prompt=youtube_prompt,
        ),
        WorkerTarget(
            key="script",
            label="NotebookLM script/body worker",
            worktree_hint="/ccidacafe/notebooklm_script",
            branch_hint="notebooklm_script",
            title_hint="notebooklm_script",
            prompt=script_prompt,
        ),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="YouTube URL to route to the Orca worker sessions")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve target terminals and print prompts without sending them",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not YOUTUBE_RE.match(args.url):
        print(f"Not a supported YouTube URL: {args.url}", file=sys.stderr)
        return 2

    terminals = list_terminals()
    targets = build_targets(args.url)
    resolved: list[tuple[WorkerTarget, dict[str, Any]]] = []
    for target in targets:
        resolved.append((target, pick_terminal(terminals, target)))

    for target, terminal in resolved:
        handle = terminal["handle"]
        print(f"{target.key}: {handle} ({terminal.get('worktreePath')})")
        if args.dry_run:
            print(target.prompt)
            print("-" * 80)
        else:
            send_prompt(handle, target.prompt)
            print(f"sent: {target.label}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
