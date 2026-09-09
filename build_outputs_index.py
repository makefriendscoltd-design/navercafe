#!/usr/bin/env python3
"""outputs 진행내역 색인(INDEX.md)을 만든다.

메인 저장소와 git worktree 전부의 outputs/ 를 훑어 CRM 장부와 대조한다.
읽기만 한다 - 파일을 옮기거나 지우지 않는다.

    .venv/bin/python build_outputs_index.py
"""

from __future__ import annotations

import collections
import datetime
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
LEDGER = (
    Path.home()
    / "Library/Application Support/AIMAX CRM Observability/events.sqlite3"
)
CHANNELS = {
    "youtube_shorts": "숏폼",
    "youtube_community": "커뮤니티",
    "naver_cafe": "카페",
}
# 장부 campaign 에서 소스 영상 ID(11자)를 뽑는다.
CAMPAIGN_RE = re.compile(
    r"(?:youtube-repurpose-|youtube-shorts-)?"
    r"([A-Za-z0-9_-]{11})"
    r"(?:-\d{8})?(?:-(?:shorts|community|cardnews))?$"
)
FOLDER_RE = re.compile(r"^([A-Za-z0-9_-]{11})-(\d{8})$")
BT = chr(96)


def code(text: str) -> str:
    return BT + text + BT


def worktree_roots() -> list[Path]:
    """메인 + 워크트리 경로. outputs 는 스크립트 위치 기준이라 전부 봐야 한다."""
    roots = [PROJECT]
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree ") :]).resolve()
            if path not in roots:
                roots.append(path)
    return [r for r in roots if (r / "outputs").is_dir()]


def read_ledger() -> tuple[dict[str, set[str]], dict[str, set[str]], int]:
    """장부에서 소스 ID별 발행 상태를 읽는다. sent=제공자 성공 확인, planned=예약만."""
    sent: dict[str, set[str]] = collections.defaultdict(set)
    planned: dict[str, set[str]] = collections.defaultdict(set)
    if not LEDGER.is_file():
        print(f"[주의] 장부가 없어 발행 상태를 붙이지 못한다: {LEDGER}")
        return sent, planned, 0
    with sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "select channel, campaign, stage from events where project='navercafe';"
        ).fetchall()
    for channel, campaign, stage in rows:
        label = CHANNELS.get(channel)
        if label is None:
            continue
        match = CAMPAIGN_RE.search(campaign or "")
        if match is None:
            continue
        (sent if stage == "sent" else planned)[match.group(1)].add(label)
    return sent, planned, len(rows)


def pinned_folders() -> dict[str, str]:
    """코드가 하드코딩한 outputs 경로를 모은다. 여기 걸린 폴더는 옮기면 안 된다."""
    pinned: dict[str, str] = {}
    for source in sorted(PROJECT.glob("*.py")):
        if source.name in {Path(__file__).name} or source.name.startswith("test_"):
            continue
        try:
            text = source.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for hit in re.findall(r'"outputs/([^"]+)"', text):
            folder = hit.split("/", 1)[0]
            if not folder or "<" in folder or "{" in folder:
                continue
            note = f"{source.name} 가 참조"
            pinned.setdefault(folder, note)
            if source.name not in pinned[folder]:
                pinned[folder] += f", {source.name}"
    return pinned


def dir_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def main() -> int:
    sent, planned, event_count = read_ledger()
    pinned = pinned_folders()
    roots = worktree_roots()

    groups: dict[str, list[tuple[int, str, str, str]]] = {
        "발행완료": [],
        "예약대기": [],
        "작업·실험": [],
        "기록없음": [],
    }
    for root in roots:
        outputs = root / "outputs"
        where = "main" if root == PROJECT else root.name
        for entry in sorted(outputs.iterdir()):
            if not entry.is_dir() or entry.name == "__pycache__":
                continue
            match = FOLDER_RE.match(entry.name)
            vid = match.group(1) if match else None
            size = dir_size(entry)
            if vid and vid in sent:
                key, chans = "발행완료", "/".join(sorted(sent[vid]))
            elif vid and vid in planned:
                key, chans = "예약대기", "/".join(sorted(planned[vid]))
            elif vid:
                key, chans = "기록없음", "-"
            else:
                key, chans = "작업·실험", "-"
            groups[key].append((size, entry.name, chans, where))

    total = sum(len(v) for v in groups.values())
    lines = [
        "# outputs 색인",
        "",
        f"생성 {datetime.date.today()} · 총 {total}개 폴더 · 수집 위치 {len(roots)}곳",
        "",
        f"다시 만들기: {code('.venv/bin/python build_outputs_index.py')}",
        "",
        "## 폴더를 옮기지 말 것",
        "",
        "아래 스크립트가 outputs 하위 경로를 하드코딩한다. 옮기거나 이름을 바꾸면 파이프라인이 깨진다.",
        f"📌 표시된 폴더가 해당하며, 이 목록은 매번 {code('*.py')} 를 다시 훑어서 만든다.",
        "",
        f"검증: {code('.venv/bin/python content_workflow_preflight.py')}",
        "",
        "## 발행 판정 근거",
        "",
        f"CRM 장부 {code(str(LEDGER).replace(str(Path.home()), '~'))} 의",
        f"{code(chr(39) + 'navercafe' + chr(39))} 프로젝트 이벤트 {event_count}건.",
        f"{code('sent')} = 제공자 성공 확인, {code('planned')} = 예약만 잡힌 상태.",
        "",
        "장부는 절대경로라 어느 워크트리에서 발행하든 한 곳에 쌓인다.",
        "반면 outputs 는 스크립트 위치 기준이라 워크트리마다 갈라지므로, 이 색인이 전부 모아 본다.",
        "",
    ]
    for key in ["발행완료", "예약대기", "작업·실험", "기록없음"]:
        rows = sorted(groups[key], reverse=True)
        size_mb = sum(r[0] for r in rows) >> 20
        lines += [
            f"## {key} — {len(rows)}개 / {size_mb}MB",
            "",
            "| 크기 | 폴더 | 발행 채널 | 위치 | 비고 |",
            "|---|---|---|---|---|",
        ]
        for size, name, chans, where in rows:
            pin = "📌 " if name in pinned else ""
            lines.append(
                f"| {size >> 20}MB | {pin}{code(name)} | {chans} | {where} "
                f"| {pinned.get(name, '')} |"
            )
        lines.append("")

    target = PROJECT / "outputs" / "INDEX.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{target} — {total}개 폴더")
    for key, rows in groups.items():
        print(f"  {key}: {len(rows)}개")
    print(f"수집 위치: {', '.join('main' if r == PROJECT else r.name for r in roots)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
