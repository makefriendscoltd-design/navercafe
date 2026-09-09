import json

import pytest

import reference_selection as sel


def candidate(**overrides):
    base = {"id": "abcDEF12345", "title": "How I Automate My Business With Claude",
            "channel": "Nate Herk | AI Automation", "minutes": 20,
            "upload_date": "2026-09-01", "first_seen": "2026-09-09"}
    base.update(overrides)
    return base


def test_a_practitioner_walkthrough_is_what_this_channel_republishes():
    assert sel.rejection_reason(candidate()) is None


@pytest.mark.parametrize("overrides, reason", [
    ({"channel": "Google Cloud Tech"}, "벤더"),
    ({"channel": "Simplilearn"}, "강의 공장형"),
    ({"title": "The 26 Best AI Tools of 2026 — Ranked"}, "툴 랭킹"),
    ({"title": "Exciting AI Updates Weekly - August 21, 2026"}, "툴 랭킹"),
    ({"title": "2026 클로드 올인원 무료 강의"}, "한국어"),
    ({"minutes": 600}, "코스 덤프"),
    ({"minutes": 3}, "짧음"),
])
def test_sources_that_do_not_fit_are_dropped_with_a_reason(overrides, reason):
    got = sel.rejection_reason(candidate(**overrides))
    assert got and reason in got


def test_a_long_but_workable_source_is_kept():
    """106 minutes already produced fine; the cut is at two hours."""
    assert sel.rejection_reason(candidate(minutes=106)) is None
    assert sel.rejection_reason(candidate(minutes=121))


def test_selection_takes_the_newest_first_and_respects_the_limit():
    rows = [candidate(id="aaaaaaaaaaa", first_seen="2026-09-01"),
            candidate(id="bbbbbbbbbbb", first_seen="2026-09-09"),
            candidate(id="ccccccccccc", first_seen="2026-09-05")]
    keep, drop = sel.select(rows, limit=2)
    assert [c["id"] for c in keep] == ["bbbbbbbbbbb", "ccccccccccc"]
    assert drop == []


def test_rejections_are_reported_rather_than_silently_dropped():
    rows = [candidate(), candidate(id="zzzzzzzzzzz", channel="Simplilearn")]
    keep, drop = sel.select(rows)
    assert len(keep) == 1 and len(drop) == 1
    assert drop[0]["reason"]


def test_an_already_produced_source_is_never_offered_again(tmp_path, monkeypatch):
    seen = tmp_path / "seen.json"
    seen.write_text(json.dumps({"videos": {
        "abcDEF12345": {"title": "t", "channel": "c", "duration": 1200},
        "zyxWVU54321": {"title": "t2", "channel": "c", "duration": 1200},
    }}), encoding="utf-8")
    monkeypatch.setattr(sel, "already_produced", lambda vid: vid == "abcDEF12345")
    ids = [c["id"] for c in sel.load_candidates(seen)]
    assert ids == ["zyxWVU54321"]


@pytest.mark.parametrize("title", [
    "Ep 850: Agent Risk, Security, and AI Sprawl in 2026",
    "Episode 12 - Building With Claude",
    "The AI Podcast: How Agents Actually Work",
])
def test_a_podcast_episode_has_no_five_steps_to_lift(title):
    assert sel.rejection_reason(candidate(title=title)) == "팟캐스트 회차"


def test_a_tutorial_that_merely_mentions_a_number_is_not_a_podcast():
    assert sel.rejection_reason(candidate(title="Build 5 AI Agents in 30 Minutes")) is None
