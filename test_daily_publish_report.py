import importlib.util
import json
import sys
from datetime import date
from pathlib import Path


MODULE_PATH = Path(__file__).parent / "outputs/cafe-publish-queue-20260823/daily_publish_report.py"
SPEC = importlib.util.spec_from_file_location("daily_publish_report", MODULE_PATH)
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_report_uses_union_and_keeps_unknown_total_label(monkeypatch, tmp_path):
    seen = tmp_path / "seen.json"
    queue = tmp_path / "queue.json"
    audit = tmp_path / "audit.json"
    write_json(seen, {"videos": {"seen-source": {
        "channel": "Independent Creator", "title": "How I automate my work",
        "duration_seconds": 700, "seen_at": "2026-09-09T12:00:00+09:00"},
        "excluded-src": {
        "channel": "Microsoft", "title": "Vendor keynote",
        "duration_seconds": 1800, "seen_at": "2026-09-09T11:00:00+09:00"},
        "overlap": {}}})
    write_json(queue, {"entries": [{"source_key": "queue-source"}, {"source_key": "overlap"}]})
    write_json(audit, {"items": [{"source_key": "submitted-source"}, {"source_key": "overlap"}]})
    monkeypatch.setattr(reporter.reference_selection, "SEEN_PATH", seen)
    monkeypatch.setattr(reporter, "QUEUE_PATH", queue)
    monkeypatch.setattr(reporter, "USER_SUBMITTED_AUDIT", audit)
    monkeypatch.setattr(reporter, "ROOT", tmp_path)

    def fake_inventory(project, keys):
        return [{"source_key": key, "channels": {
            "cafe": {"status": "published", "verified": True},
            "shorts": {"status": "complete", "verified": key == "overlap"},
            "community": {"status": "published", "verified": key == "overlap"},
        }, "complete": key == "overlap", "root": str(tmp_path / key)} for key in sorted(keys)]

    monkeypatch.setattr(reporter.content_run_state, "inventory", fake_inventory)
    report = reporter._build_report(date(2026, 9, 9), 2)

    assert report["universe"]["known_unique"] == 5
    assert "전체 제출 건수로 단정하지 않음" in report["universe"]["label"]
    assert report["progress"] == {
        "complete_3_channels": 1, "actionable_incomplete": 2,
        "discovered_selection_pending": 1, "discovered_condition_excluded": 1,
        "channel_verified": {"cafe": 5, "shorts": 1, "community": 1},
    }
    assert next(x for x in report["sources"] if x["source_key"] == "seen-source")[
        "next_action"] == "발견 후 선정 대기"
    assert next(x for x in report["sources"] if x["source_key"] == "queue-source")[
        "next_action"] == "증거 미연결/미검증 채널: shorts, community"
    assert next(x for x in report["sources"] if x["source_key"] == "excluded-src")[
        "next_action"] == "조건 제외: 벤더·컨퍼런스 발표"


def test_long_incomplete_list_is_chunked_without_losing_sources():
    sources = [{"source_key": f"source-{i:03d}", "complete": False, "origin": "queue",
                "next_action": "증거 미연결/미검증 채널: cafe, shorts, community",
                "channels": {name: {"status": "missing", "verified": False}
                             for name in ("cafe", "shorts", "community")}}
               for i in range(80)]
    report = {"report_date": "2026-09-09", "cafe_published_yesterday": 2,
              "universe": {"known_unique": 80},
              "progress": {"complete_3_channels": 0, "actionable_incomplete": 80,
                           "discovered_selection_pending": 0,
                           "discovered_condition_excluded": 0,
                           "channel_verified": {"cafe": 0, "shorts": 0, "community": 0}},
              "sources": sources}
    chunks = reporter._messages(report)

    assert len(chunks) > 1
    assert all(len(chunk) <= 3500 for chunk in chunks)
    combined = "\n".join(chunks)
    assert all(source["source_key"] in combined for source in sources)


def test_existing_duplicate_report_state_round_trips(tmp_path):
    state = tmp_path / "state.json"
    original = {"last_reported_date": "2026-09-09", "send_count": 3}
    reporter._write_state(state, original)
    assert reporter._load_state(state) == original


def test_already_reported_date_does_not_send_again(monkeypatch, tmp_path, capsys):
    state = tmp_path / "state.json"
    write_json(state, {"last_reported_date": "2026-09-09", "send_count": 3})
    report = {"universe": {"known_unique": 4},
              "progress": {"complete_3_channels": 1, "actionable_incomplete": 3}}
    monkeypatch.setattr(reporter, "_load_events", lambda _: [])
    monkeypatch.setattr(reporter, "_build_report", lambda *args: report)
    monkeypatch.setattr(reporter, "_write_report", lambda *args: tmp_path / "report.json")
    monkeypatch.setattr(reporter, "_messages", lambda _: ["one", "two", "three"])
    monkeypatch.setattr(sys, "argv", ["daily_publish_report.py", "--date", "2026-09-09",
                                      "--state", str(state), "--send"])

    assert reporter.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "already_reported"
