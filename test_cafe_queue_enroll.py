from datetime import datetime
from zoneinfo import ZoneInfo

from cafe_queue_enroll import next_slot


KST = ZoneInfo("Asia/Seoul")


def policy(entries):
    return {
        "windows": [f"{hour:02d}:00" for hour in range(10, 24)],
        "maximum_successes_per_day": 2,
        "minimum_gap_hours": 5,
        "entries": entries,
    }


def pending(source_key, not_before, **extra):
    return {"source_key": source_key, "status": "pending",
            "not_before": not_before, **extra}


def test_next_slot_uses_daily_capacity_and_minimum_gap():
    queue = policy([
        pending("first", "2026-09-10T10:00:00+09:00"),
    ])
    now = datetime(2026, 9, 10, 9, 0, tzinfo=KST)

    assert next_slot(now, queue) == datetime(2026, 9, 10, 15, 0, tzinfo=KST)


def test_next_slot_rolls_to_next_window_when_latest_day_is_full():
    queue = policy([
        pending("first", "2026-09-25T10:00:00+09:00"),
        pending("second", "2026-09-25T15:00:00+09:00"),
        # Preserve a pre-existing over-cap reservation without extending it.
        pending("legacy-overflow", "2026-09-25T16:00:00+09:00"),
    ])
    now = datetime(2026, 9, 10, 0, 0, tzinfo=KST)

    assert next_slot(now, queue) == datetime(2026, 9, 26, 10, 0, tzinfo=KST)


def test_next_slot_ignores_completed_and_locked_entries():
    queue = policy([
        {**pending("published", "2026-09-30T15:00:00+09:00"),
         "status": "published", "published_url": "https://cafe.naver.com/westudyssat/1"},
        {**pending("locked", "2026-09-29T15:00:00+09:00"),
         "do_not_retry": True},
    ])
    now = datetime(2026, 9, 10, 9, 5, tzinfo=KST)

    assert next_slot(now, queue) == datetime(2026, 9, 10, 10, 0, tzinfo=KST)


def test_new_source_follows_replanned_failed_and_pending_reservations():
    queue = policy([
        {**pending("retry", "2026-09-01T10:00:00+09:00"), "status": "failed",
         "planned_publish_at": "2026-10-24T10:05:00+09:00"},
        pending("next", "2026-09-02T10:00:00+09:00",
                planned_publish_at="2026-10-24T16:05:00+09:00"),
    ])
    assert next_slot(datetime(2026,9,22,11,tzinfo=KST), queue) == datetime(2026,10,25,10,tzinfo=KST)
