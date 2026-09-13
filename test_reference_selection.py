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


def test_an_output_folder_alone_is_not_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(sel, "PROJECT", tmp_path)
    (tmp_path / "outputs/abcDEF12345-20260909").mkdir(parents=True)
    assert sel.already_produced("abcDEF12345") is False


@pytest.mark.parametrize("title", [
    "Ep 850: Agent Risk, Security, and AI Sprawl in 2026",
    "Episode 12 - Building With Claude",
    "The AI Podcast: How Agents Actually Work",
])
def test_a_podcast_episode_has_no_five_steps_to_lift(title):
    assert sel.rejection_reason(candidate(title=title)) == "팟캐스트 회차"


def test_a_tutorial_that_merely_mentions_a_number_is_not_a_podcast():
    assert sel.rejection_reason(candidate(title="Build 5 AI Agents in 30 Minutes")) is None


def test_current_seen_schema_uses_duration_seconds_and_seen_at(tmp_path, monkeypatch):
    seen = tmp_path / "seen.json"
    seen.write_text(json.dumps({"videos": {"abcDEF12345": {
        "title": "How I Automate My Business", "channel": "Practitioner",
        "duration_seconds": 1200, "seen_at": "2026-09-09T02:06:50+00:00",
        "upload_date": "20260908"}}}), encoding="utf-8")
    monkeypatch.setattr(sel, "already_produced", lambda key: False)
    row = sel.load_candidates(seen)[0]
    assert row["minutes"] == 20
    assert row["first_seen"].startswith("2026-09-09")


def test_missing_duration_is_rejected_instead_of_bypassing_bounds():
    assert sel.rejection_reason(candidate(minutes=None)) == "영상 길이 미확인"


def test_eight_minute_boundary_uses_seconds_without_rounding_up():
    assert sel.rejection_reason(candidate(minutes=8, duration_seconds=479)) == "7분으로 너무 짧음"
    assert sel.rejection_reason(candidate(minutes=8, duration_seconds=480)) is None


def test_seen_timestamps_sort_by_instant_not_offset_text():
    assert sel._date_key("2026-09-09T10:00:00+09:00") < sel._date_key("2026-09-09T02:00:00+00:00")


def test_new_sources_come_before_revisits():
    """A revisit's Cafe post is already queued, so it adds nothing to the
    schedule until that slot comes round; new sources fill the empty ones."""
    rows = [candidate(id="retryOld01", has_output=True, last_attempt="2026-09-08T10:00:00+09:00"),
            candidate(id="retryNew02", has_output=True, last_attempt="2026-09-09T10:00:00+09:00"),
            candidate(id="freshNew003", first_seen="2026-09-10", has_output=False),
            candidate(id="freshOld004", first_seen="2026-09-09", has_output=False)]
    keep, _ = sel.select(rows, limit=2)
    assert [row["id"] for row in keep] == ["freshNew003", "freshOld004"]
    # Revisits stay reachable, oldest attempt first, once the new ones run out.
    assert [row["id"] for row in sel.select(rows)[0]][-2:] == ["retryOld01", "retryNew02"]


def test_limit_one_takes_the_newest_unproduced_source():
    rows = [candidate(id="retryOld01", has_output=True, last_attempt="2026-09-08"),
            candidate(id="freshNew003", has_output=False)]
    assert sel.select(rows, limit=1)[0][0]["id"] == "freshNew003"


def test_revisits_are_still_selected_when_nothing_is_new():
    rows = [candidate(id="retryOld01", has_output=True, last_attempt="2026-09-08")]
    assert [row["id"] for row in sel.select(rows, limit=2)[0]] == ["retryOld01"]


def test_all_four_failed_outputs_remain_retry_candidates():
    rows = [candidate(id=key, has_output=True, last_attempt="") for key in
            ["ZzHsJW10iq4", "xlaSgpb_9hg", "_A80xAMOyxk", "I4kGV5sJEdA"]]
    selected, rejected = sel.select(rows)
    assert {row["id"] for row in selected} == {row["id"] for row in rows}
    assert rejected == []
