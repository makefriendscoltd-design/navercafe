import json
from datetime import datetime, timezone

import pytest

import cafe_queue_runner as runner


@pytest.mark.parametrize("url", [
    "https://cafe.naver.com/westudyssat/6168",
    "https://cafe.naver.com/f-e/cafes/26321967/articles/6168",
    "https://cafe.naver.com/westudyssat?iframe_url_utf8=%2FArticleRead.nhn%253Fclubid%3D26321967%2526articleid%3D6168",
])
def test_provider_receipt_urls_reconcile_to_same_article(tmp_path, url):
    (tmp_path / "evidence.json").write_text(json.dumps({"providerUrl": url}))
    assert runner.read_provider_url(tmp_path, {"provider_evidence": "evidence.json"}) == \
        "https://cafe.naver.com/westudyssat/6168"


def test_missing_provider_url_cannot_mark_queue_success(tmp_path):
    path = tmp_path / "queue.json"
    original = {"entries": [{"source_key": "source", "status": "failed"}]}
    path.write_text(json.dumps(original))
    with pytest.raises(RuntimeError, match="no verified provider"):
        runner.record(tmp_path, path, "source", datetime.now(timezone.utc), True, "")
    assert json.loads(path.read_text()) == original


def test_backlog_plan_respects_successes_daily_cap_and_weekends():
    now = datetime(2026, 9, 22, 11, tzinfo=runner.KST)
    queue = {"maximum_successes_per_day": 2, "minimum_gap_hours": 5,
             "planning_buffer_minutes": 30, "entries": [
                 {"source_key": "done", "status": "published", "last_attempt_at": "2026-09-22T10:49:45+09:00"},
                 *[{"source_key": str(i), "status": "pending", "not_before": "2026-09-01T10:00:00+09:00"} for i in range(61)],
             ]}
    plan = runner.plan_remaining(queue, now, [(h, 5) for h in range(10, 24)])
    stamps = [datetime.fromisoformat(s) for s in plan.values()]
    assert len(stamps) == 61
    assert stamps[0].isoformat() == "2026-09-22T16:05:00+09:00"
    assert all((b-a).total_seconds() >= 5.5*3600 for a,b in zip(stamps, stamps[1:]))
    assert max(sum(s.date() == day.date() for s in stamps) for day in stamps) <= 2
    assert any(s.weekday() == 6 for s in stamps)
    assert not runner.due_entries({"entries": [{"status":"pending", "source_key":"x",
        "not_before":"2026-09-01T10:00:00+09:00", "planned_publish_at":stamps[0].isoformat()}]}, now)


def test_prepare_lookahead_never_saves_beyond_next_two_articles(tmp_path):
    entries = []
    for i in range(3):
        manifest = tmp_path / str(i) / "cafe/06_cafe_manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("{}")
        entries.append({"source_key":str(i), "status":"pending", "manifest":str(manifest),
                        "not_before":f"2026-09-{23+i}T10:00:00+09:00"})
        if i < 2:
            (manifest.parent / "provider").mkdir()
            (manifest.parent / "provider/11_verified_draft.json").write_text("{}")
    assert runner.draft_candidates(tmp_path, {"entries":entries,"draft_lookahead":2},
                                   datetime(2026,9,22,tzinfo=runner.KST)) == []


def test_preparation_records_draft_without_marking_article_published(tmp_path, monkeypatch):
    from types import SimpleNamespace
    manifest = tmp_path / "source/cafe/06_cafe_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    queue = {"draft_lookahead":2,"entries":[{"source_key":"source","status":"pending",
        "manifest":str(manifest),"not_before":"2026-09-23T10:00:00+09:00"}]}
    path = tmp_path / "queue.json"
    path.write_text(json.dumps(queue))
    def run(command, **kwargs):
        assert command[-1] == "--prepare-only"
        (manifest.parent / "provider").mkdir()
        (manifest.parent / "provider/11_verified_draft.json").write_text(
            json.dumps({"draft":{"status":"draft_saved"}}))
        return SimpleNamespace(returncode=0,stdout="saved",stderr="")
    monkeypatch.setattr(runner.subprocess,"run",run)
    result=runner.prepare_ahead(tmp_path,path,queue,datetime(2026,9,22,tzinfo=runner.KST),60)
    assert result["ok"]
    current=json.loads(path.read_text())["entries"][0]
    assert current["status"] == "pending"
    assert current["draft_prepared_at"]
    assert "published_url" not in current
