"""Send one daily Naver Cafe publication-count report to the verified private chat."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import naver_cafe_draft_scheduler as scheduler
import content_run_state
import reference_selection
from external_publish_tracking import track_external_event
from telegram_publish_approval import TelegramApproval, load_approval_credentials


KST = ZoneInfo("Asia/Seoul")
TRACKER = Path(
    "/Users/apple/orca/projects/aimax-crm-observability/bin/aimax-crm-track"
)
DEFAULT_STATE = Path(__file__).with_name("daily_publish_report_state.json")
DEFAULT_REPORT_DIR = Path(__file__).with_name("daily-reports")
USER_SUBMITTED_AUDIT = ROOT / "outputs/reference-link-routine/user-submitted-link-audit.json"
QUEUE_PATH = Path(__file__).with_name("queue.json")
TELEGRAM_TEXT_LIMIT = 3500


def _report_date(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(KST).date() - timedelta(days=1)


def _load_events(report_date: date) -> list[dict]:
    completed = subprocess.run(
        [str(TRACKER), "export-json", "--date", report_date.isoformat()],
        check=True,
        capture_output=True,
        text=True,
    )
    events = json.loads(completed.stdout)
    if not isinstance(events, list):
        raise RuntimeError("CRM 내보내기 결과가 목록이 아닙니다.")
    return events


def _published_count(events: list[dict]) -> int:
    return sum(
        max(0, int(event.get("metric_count") or 0))
        for event in events
        if event.get("channel") == "naver_cafe"
        and event.get("stage") == "sent"
        and event.get("status") == "success"
    )


def _load_state(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _universe() -> tuple[dict[str, dict], dict[str, int]]:
    seen = _load_state(reference_selection.SEEN_PATH).get("videos") or {}
    queue = _load_state(QUEUE_PATH).get("entries") or []
    submitted = _load_state(USER_SUBMITTED_AUDIT).get("items") or []
    output_keys = {
        match.group(1) for path in (ROOT / "outputs").glob("*-20??????")
        if path.is_dir() and (match := re.fullmatch(r"(.{11})-20\d{6}", path.name))
    }
    groups = {
        "discovered": {str(key) for key in seen if key},
        "queue": {str(row.get("source_key")) for row in queue if row.get("source_key")},
        "actual_output": output_keys,
        "direct_submitted": {
            str(row.get("source_key")) for row in submitted if row.get("source_key")
        },
    }
    keys = set().union(*groups.values())
    origins = {}
    for key in keys:
        if key in groups["direct_submitted"]:
            origin = "direct_submitted"
        elif key in groups["queue"]:
            origin = "queue"
        elif key in groups["actual_output"]:
            origin = "actual_output"
        else:
            origin = "discovered_only"
        meta = seen.get(key) if isinstance(seen.get(key), dict) else {}
        raw_seconds = meta.get("duration_seconds")
        if raw_seconds is None:
            raw_seconds = meta.get("duration")
        candidate = {"channel": meta.get("channel"), "title": meta.get("title"),
                     "duration_seconds": raw_seconds}
        title = str(meta.get('title') or '')
        if not title:
            for path in (ROOT / 'outputs').glob(key + '-20??????/cafe/06_cafe_manifest.json'):
                title = str(_load_state(path).get('title') or '')
                if title:
                    break
        origins[key] = {"origin": origin, "title": title,
                        "selection_exclusion": (reference_selection.rejection_reason(candidate)
                                                if origin == "discovered_only" else None)}
    return origins, {name: len(values) for name, values in groups.items()}


def _next_action(row: dict) -> str:
    if row["origin"] == "discovered_only":
        reason = row.get("selection_exclusion")
        return f"조건 제외: {reason}" if reason else "발견 후 선정 대기"
    if row.get('needs_review'):
        return '손상·부분 산출물 보존, 복구 검토 필요'
    if row.get('terminal_blocked'):
        return '정지 원본으로 쇼츠 차단, 다른 채널 상태 유지'
    community = row['channels'].get('community', {})
    if community.get('protected'):
        return '기존 커뮤니티 예약 증거 재확인 필요, 재게시 금지'
    cafe = row['channels'].get('cafe', {})
    if row.get('handed_off') and not cafe.get('verified'):
        return '카페 큐 대기: ' + str(cafe.get('next_eligible_at') or '다음 허용 시각 확인')
    missing = [name for name, state in row["channels"].items() if not state["verified"]]
    return "3채널 공급자 검증 완료" if not missing else "증거 미연결/미검증 채널: " + ", ".join(missing)


def _build_report(report_date: date, published_count: int) -> dict:
    origins, coverage = _universe()
    rows = content_run_state.inventory(ROOT, origins)
    for row in rows:
        row.update(origins[row["source_key"]])
        row["next_action"] = _next_action(row)
    complete = sum(row["complete"] for row in rows)
    actionable = [row for row in rows if row["origin"] != "discovered_only"]
    discovered = [row for row in rows if row["origin"] == "discovered_only"]
    channel_counts = {
        name: sum(row["channels"][name]["verified"] for row in rows)
        for name in ("cafe", "shorts", "community")
    }
    return {
        "schema_version": "content-daily-progress/v1",
        "report_date": report_date.isoformat(),
        "generated_at": datetime.now(KST).isoformat(),
        "cafe_published_yesterday": published_count,
        "cafe_count_basis": "provider URL 확인 후 CRM sent/success로 기록된 전일 건수",
        "universe": {
            "label": "로컬에서 확인된 source_key 합집합 (전체 제출 건수로 단정하지 않음)",
            "known_unique": len(origins), "inputs": coverage,
        },
        "progress": {"complete_3_channels": complete,
                     "actionable_incomplete": sum(not row["complete"] for row in actionable),
                     "discovered_selection_pending": sum(
                         not row.get("selection_exclusion") for row in discovered),
                     "discovered_condition_excluded": sum(
                         bool(row.get("selection_exclusion")) for row in discovered),
                     "channel_verified": channel_counts},
        "sources": rows,
    }


def _messages(report: dict) -> list[str]:
    progress = report["progress"]
    universe = report["universe"]
    header = (
        "[AIMAX 콘텐츠 일일 진행 보고]\n"
        f"기준일: {report['report_date']}\n"
        f"카페 전일 실제 발행: {report['cafe_published_yesterday']}건\n"
        f"확인된 source_key: {universe['known_unique']}건 (전체 제출 분모 아님)\n"
        f"3채널 완료: {progress['complete_3_channels']}건 · 확인 필요: {progress['actionable_incomplete']}건\n"
        f"발견 후 선정 대기: {progress['discovered_selection_pending']}건 · 조건 제외: {progress['discovered_condition_excluded']}건\n"
        "채널 검증: 카페 {cafe} · 쇼츠 {shorts} · 커뮤니티 {community}\n"
        "미완료 목록:"
    ).format(**progress["channel_verified"])
    lines = [
        f"{row.get('title') or row['source_key']}\n{row['source_key']} · {row['origin']} · "
        + "/".join(f"{name}:{'검증됨' if state['verified'] else '미검증'}"
                   for name, state in row["channels"].items())
        + f" · {row['next_action']}"
        for row in report["sources"]
        if not row["complete"] and row["origin"] != "discovered_only"
    ] or ["없음"]
    chunks, current = [], header
    for line in lines:
        candidate = current + "\n" + line
        if len(candidate) > TELEGRAM_TEXT_LIMIT:
            chunks.append(current)
            current = "[3채널 진행 보고 계속]\n" + line
        else:
            current = candidate
    chunks.append(current)
    if any(len(chunk) > TELEGRAM_TEXT_LIMIT for chunk in chunks):
        raise RuntimeError("텔레그램 보고 청크가 3,500자를 넘습니다.")
    return chunks


def _write_report(report_dir: Path, report: dict) -> Path:
    path = report_dir / f"{report['report_date']}.json"
    _write_state(path, report)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="네이버 카페 전일 발행량 개인 보고")
    parser.add_argument("--date")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()

    report_date = _report_date(args.date)
    events = _load_events(report_date)
    published_count = _published_count(events)
    report = _build_report(report_date, published_count)
    report_path = _write_report(args.report_dir, report)
    messages = _messages(report)
    output = {
        "status": "dry_run",
        "report_date": report_date.isoformat(),
        "published_count": published_count,
        "destination": "CCIDA private",
        "report_path": str(report_path),
        "known_source_count": report["universe"]["known_unique"],
        "complete_3_channels": report["progress"]["complete_3_channels"],
        "incomplete_count": report["progress"]["actionable_incomplete"],
        "expected_send_count": len(messages),
    }
    if not args.send:
        print(json.dumps(output, ensure_ascii=False))
        return 0

    state = _load_state(args.state)
    if state.get("last_reported_date") == report_date.isoformat():
        output["status"] = "already_reported"
        print(json.dumps(output, ensure_ascii=False))
        return 0

    config = scheduler._load_config()
    credentials = load_approval_credentials(config)
    client = TelegramApproval(str(credentials["token"]), str(credentials["chat_id"]))
    destination = client.verify_destination(
        expected_bot=str(credentials["expected_bot"]),
        expected_chat_type="private",
    )
    if destination.get("chat_type") != "private":
        raise RuntimeError("CCIDA 개인 채팅 검증에 실패했습니다.")

    sent_count = sum(client.send_text(message) for message in messages)
    if sent_count != len(messages):
        raise RuntimeError(
            f"일일 보고 전송 수가 다릅니다: expected={len(messages)}, actual={sent_count}"
        )
    tracked = track_external_event(
        "telegram",
        f"naver-cafe-daily-publish-report:{report_date.isoformat()}",
        campaign="naver-cafe-daily-publish-report",
        stage="sent",
        count=sent_count,
    )
    _write_state(
        args.state,
        {
            "last_reported_date": report_date.isoformat(),
            "reported_at": datetime.now(KST).isoformat(),
            "published_count": published_count,
            "report_path": str(report_path),
            "send_count": sent_count,
            "crm_tracked": bool(tracked),
        },
    )
    output.update({"status": "sent", "send_count": sent_count,
                   "crm_tracked": bool(tracked)})
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
