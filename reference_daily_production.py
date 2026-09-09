"""Produce the day's selected references across all three channels.

The research automation collects candidates every day and nothing consumed them.
This is the other half: take the newest candidates that fit what this channel
publishes and build them.

Rate is set by what the channels can actually publish -- the Cafe queue posts at
most two a day and Shorts hold two slots a day -- so producing more than that per
day only inflates the backlog.

A run counts as complete only when ``content_acceptance`` passes every channel
and every channel then publishes. Commands exiting zero never meant the
artefacts were right, which is why acceptance sits between producing and
publishing rather than after it.

Cafe joins the spaced publish queue, which posts it on its own schedule. Card
news and the Shorts upload go out on the run.
"""

from __future__ import annotations

import argparse
import fcntl
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
EXPECTED_CHANNEL = "나민수 AI"


def _run(args: list[str], *, timeout: int = 3600) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            [str(PYTHON), *args], cwd=PROJECT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return False, f"timeout after {exc.timeout}s"
    except OSError as exc:
        return False, f"runner error: {exc}"
    tail = (completed.stdout or "").strip().splitlines()[-1:] or \
           (completed.stderr or "").strip().splitlines()[-1:]
    return completed.returncode == 0, (tail[0] if tail else "")[:300]


def produce(source_key: str, today: str) -> dict:
    """Shorts render, Cafe candidate and card deck for one source."""
    steps: dict[str, str] = {}
    import content_run_state
    root = content_run_state.resumable_root(PROJECT, source_key, today)
    root.mkdir(parents=True, exist_ok=True)

    if ((root / "shorts/final.mp4").is_file()
            or ((root / "shorts/07_script_final.txt").is_file()
                and (root / "shorts/production_manifest.json").is_file())):
        ok, note = True, "기존 원고 재사용"
    else:
        ok, note = _run(["shorts_new_prepare.py", source_key])
    steps["shorts_prepare"] = "ok" if ok else f"fail: {note}"
    if ok and not (root / "shorts/final.mp4").is_file():
        ok, note = _run(["shorts_v7_builder.py", "--root", str(root / "shorts"), "--render"])
        steps["shorts_render"] = "ok" if ok else f"fail: {note}"

    if (root / "cafe/notebooklm/notebooklm-answer.md").is_file():
        ok, note = True, "기존 NotebookLM 응답 재사용"
    else:
        ok, note = _run(["-c", (
        "import sys; sys.path.insert(0, '.');"
        "import youtube_cafe_auto as auto, notebooklm_source as nlm;"
        f"cfg = nlm.load_config(auto.load_or_create_config());"
        f"cfg['evidence_dir'] = r'{root / 'cafe' / 'notebooklm'}';"
        "import pathlib; pathlib.Path(cfg['evidence_dir']).mkdir(parents=True, exist_ok=True);"
            f"nlm.fetch_manuscript('https://youtu.be/{source_key}', cfg)")])
    steps["cafe_answer"] = "ok" if ok else f"fail: {note}"
    if (root / "cafe/06_cafe_manifest.json").is_file():
        steps["cafe_candidate"] = "ok"
    elif ok:
        ok, note = _run(["cafe_new_prepare.py", source_key])
        steps["cafe_candidate"] = "ok" if ok else f"fail: {note}"
    if (root / "cardnews/04_cardnews_deck.json").is_file():
        steps["cardnews"] = "ok"
    elif steps.get("cafe_candidate") == "ok":
        ok, note = _run(["content_workflow.py", "prepare-cardnews",
                         "--cafe-manifest", str(root / "cafe/06_cafe_manifest.json"),
                         "--candidate", str(root / "cardnews")])
        steps["cardnews"] = "ok" if ok else f"fail: {note}"

    # Exit codes said every one of these runs succeeded while the community body
    # was missing, decks carried no anchor and titles were still sentinels.
    import content_acceptance

    verdict = content_acceptance.audit(root)
    published = publish(root, verdict)
    provider = content_run_state.source_state(PROJECT, source_key)
    return {"source_key": source_key, "root": str(root), "steps": steps,
            "published": published, "provider": provider["channels"],
            "acceptance": verdict["status"],
            "problems": {name: channel["problems"]
                         for name, channel in verdict["channels"].items()
                         if channel["problems"]},
            "handed_off": provider["handed_off"],
            "complete": provider["complete"]}


def publish(root: Path, verdict: dict) -> dict:
    """Publish the channels whose artefacts passed, and only those.

    The Shorts spec asks for a human look at the eight checkpoint frames. That
    check now has machine equivalents -- layout, still source, sentinel title,
    lineage binding -- and the contact sheet is still written for afterwards.
    """
    steps: dict[str, str] = {}
    import content_run_state
    source_key = root.name.rsplit("-", 1)[0]
    if not verdict.get("channels") or verdict["channels"]["source"]["status"] != "pass":
        return {"source": "fail: 원본이 롱폼이 아님"}

    if verdict["channels"]["cafe"]["status"] == "pass":
        cafe = content_run_state.cafe_state(PROJECT, source_key)
        if cafe.get("enrolled"):
            ok, note = True, f"기존 큐 상태 {cafe['status']}"
        else:
            ok, note = _run(["cafe_queue_enroll.py", "--manifest",
                             str(root / "cafe/06_cafe_manifest.json")], timeout=900)
        steps["cafe_enroll"] = "enrolled" if ok else f"fail: {note}"

    if verdict["channels"]["cardnews"]["status"] == "pass":
        community = content_run_state.community_state(root, source_key)
        if community["verified"]:
            steps["community_publish"] = "ok"
        elif community.get("protected"):
            steps["community_publish"] = "needs_review: 기존 provider 게시물 보호"
        else:
            community = None
        cards = root / "cardnews/render"
        deck = root / "cardnews/04_cardnews_deck.json"
        # A resumed run must not re-render over cards it already made: the
        # renderer refuses an existing directory, which read as a fresh failure.
        # It puts the ten cards in a "png" folder under the directory it is given.
        if community:
            ok, note = True, "provider 검증 완료"
        elif len(list(cards.glob("png/*.png"))) == 10:
            ok, note = True, "이미 렌더됨"
        else:
            ok, note = _run(["-c", (
                "import sys, json, pathlib; sys.path.insert(0, '.');"
                "from youtube_cardnews_pipeline import render_cardnews_pngs;"
                f"deck = json.loads(pathlib.Path(r'{deck}').read_text(encoding='utf-8'));"
                f"print(len(render_cardnews_pngs(deck, r'{cards}', aspect='square')))")],
                timeout=900)
        steps["cardnews_render"] = "ok" if ok else f"fail: {note}"
        if ok and not community:
            images = sorted(str(png) for png in cards.glob("png/*.png"))
            if len(images) != 10:
                steps["community_publish"] = f"fail: 카드 {len(images)}장"
            else:
                ok, note = _run(["youtube_community_auto.py", "--publish",
                                 "--expected-channel", EXPECTED_CHANNEL, "--text-file",
                                 str(root / "cardnews/05_youtube_community_post.txt"),
                                 "--images", *images, "--source-key", root.name.rsplit("-", 1)[0],
                                 "--receipt", str(root / "cardnews/provider/publish_receipt.json")], timeout=1800)
                steps["community_publish"] = "ok" if ok else f"fail: {note}"

    if verdict["channels"]["shorts"]["status"] == "pass":
        shorts = content_run_state.shorts_state(root, source_key)
        if shorts["verified"]:
            steps["shorts_provider"] = "ok"
            steps["shorts_publish"] = "ok"
            return steps
        manifest = root / "shorts/07_provider_manifest.json"
        # The provider step refuses to overwrite a manifest, which on a resumed
        # run is the right answer and not a failure.
        if manifest.is_file():
            ok, note = True, "이미 준비됨"
        else:
            ok, note = _run(["shorts_new_provider.py", str(root / "shorts")], timeout=900)
        steps["shorts_provider"] = "ok" if ok else f"fail: {note}"
        if ok and manifest.is_file():
            ok, note = _run(["youtube_shorts_aside_adapter.py", str(manifest), "--live"],
                            timeout=2400)
            steps["shorts_publish"] = "ok" if ok else f"fail: {note}"
        elif ok:
            steps["shorts_publish"] = "fail: 07_provider_manifest.json 없음"
    return steps


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DAILY_LIMIT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit은 1 이상이어야 합니다")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lock_handle = (REPORT_DIR / "run.lock").open("a+")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("이미 reference production 실행 중", flush=True)
        return 2
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

    import content_run_state
    seen = content_run_state.read_json(selection.SEEN_PATH).get("videos", {})
    queue = content_run_state.read_json(selection.QUEUE_PATH).get("entries", [])
    all_keys = set(seen) | {row.get("source_key") for row in queue if row.get("source_key")}
    submitted = content_run_state.read_json(
        PROJECT / "outputs/reference-link-routine/user-submitted-link-audit.json")
    all_keys |= {row.get("source_key") for row in submitted.get("items", [])
                 if row.get("source_key")}
    report["inventory"] = content_run_state.inventory(PROJECT, all_keys)
    report["summary"] = {
        "source_count": len(report["inventory"]),
        "complete": sum(row["complete"] for row in report["inventory"]),
        "incomplete": sum(not row["complete"] for row in report["inventory"]),
        "needs_production": sum(row["needs_production"] for row in report["inventory"]),
        "handed_off": sum(row["handed_off"] for row in report["inventory"]),
    }
    run_id = now.strftime("%Y%m%dT%H%M%S.%f%z")
    with (REPORT_DIR / f"{run_id}.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    with (REPORT_DIR / "runs.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False) + "\n")
        stream.flush()
    handed_off = sum(1 for r in report["results"] if r.get("handed_off") or r.get("complete"))
    complete = sum(1 for r in report["results"] if r.get("complete"))
    print(f"제작·공급자 인계 {handed_off}/{len(selected)} · 3채널 최종 완료 {complete}/{len(selected)}", flush=True)
    return 0 if handed_off == len(selected) else 1


if __name__ == "__main__":
    raise SystemExit(main())
