from datetime import datetime, timezone
from unittest.mock import Mock
import pytest
import cafe_shorts_alignment as alignment
import content_queue_guard as guard
import cafe_manifest_publisher as publisher
import youtube_shorts_inventory as inventory


def test_fresh_inventory_never_contacts_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(alignment, "inventory_is_fresh", lambda *args: True)
    capture = Mock()
    monkeypatch.setattr(inventory, "capture", capture)
    guard.refresh_shorts_inventory({}, datetime.now(timezone.utc))
    capture.assert_not_called()


def test_stale_inventory_refreshes_once_under_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "PROJECT", tmp_path)
    monkeypatch.setattr(publisher, "LOCK", tmp_path / "lock")
    fresh = Mock(side_effect=[False, False, True])
    monkeypatch.setattr(alignment, "inventory_is_fresh", fresh)
    capture = Mock(return_value={"status": "pass"})
    monkeypatch.setattr(inventory, "capture", capture)
    guard.refresh_shorts_inventory({}, datetime.now(timezone.utc))
    capture.assert_called_once()


def test_failed_refresh_stops_before_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "PROJECT", tmp_path)
    monkeypatch.setattr(publisher, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(alignment, "inventory_is_fresh", lambda *args: False)
    capture = Mock(return_value={"status": "blocked"})
    monkeypatch.setattr(inventory, "capture", capture)
    with pytest.raises(RuntimeError, match="refresh_failed"):
        guard.refresh_shorts_inventory({}, datetime.now(timezone.utc))
    capture.assert_called_once()
