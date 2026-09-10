import json

import content_run_state as state


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_folder_and_cafe_enrollment_are_not_three_channel_completion(tmp_path):
    root = tmp_path / "outputs/abcDEF12345-20260909"
    root.mkdir(parents=True)
    write(tmp_path / "outputs/cafe-publish-queue-20260823/queue.json",
          {"entries": [{"source_key": "abcDEF12345", "status": "pending"}]})
    got = state.source_state(tmp_path, "abcDEF12345")
    assert got["complete"] is False
    assert got["channels"]["cafe"]["enrolled"] is True
    assert got["channels"]["cafe"]["verified"] is False
    assert got["needs_production"] is True


def test_completion_requires_verified_provider_proof_for_every_channel(tmp_path):
    root = tmp_path / "outputs/abcDEF12345-20260909"
    write(tmp_path / "outputs/cafe-publish-queue-20260823/queue.json",
          {"entries": [{"source_key": "abcDEF12345", "status": "published",
                        "published_url": "https://cafe.naver.com/x/1"}]})
    write(root / "shorts/provider/journal.json",
          {"source_key": "abcDEF12345", "status": "complete",
           "verified": {"shorts_url": "https://youtube.com/shorts/x",
                        "checks": {"source": True, "status": True}}})
    write(root / "cardnews/provider/publish_receipt.json",
          {"source_key": "abcDEF12345", "status": "published", "verified": True,
           "url": "https://youtube.com/post/x"})
    assert state.source_state(tmp_path, "abcDEF12345")["complete"] is True


def test_cafe_queue_wait_does_not_require_reproduction(tmp_path):
    root = tmp_path / "outputs/abcDEF12345-20260909"
    write(tmp_path / "outputs/cafe-publish-queue-20260823/queue.json",
          {"entries": [{"source_key": "abcDEF12345", "status": "pending"}]})
    write(root / "shorts/provider/journal.json",
          {"source_key": "abcDEF12345", "status": "complete",
           "verified": {"shorts_url": "https://youtube.com/shorts/x",
                        "checks": {"source": True}}})
    write(root / "cardnews/provider/publish_receipt.json",
          {"source_key": "abcDEF12345", "status": "published", "verified": True,
           "url": "https://youtube.com/post/x"})
    got = state.source_state(tmp_path, "abcDEF12345")
    assert got["complete"] is False
    assert got["handed_off"] is True
    assert got["needs_production"] is False


def test_inventory_is_unique_and_stable(tmp_path):
    rows = state.inventory(tmp_path, ["bbbDEF12345", "aaaDEF12345", "bbbDEF12345"])
    assert [row["source_key"] for row in rows] == ["aaaDEF12345", "bbbDEF12345"]


def test_empty_shorts_checks_never_verify(tmp_path):
    root = tmp_path / "outputs/abcDEF12345-20260909"
    write(root / "shorts/provider/journal.json",
          {"source_key": "abcDEF12345", "status": "complete",
           "verified": {"shorts_url": "https://youtube.com/shorts/x", "checks": {}}})
    assert state.shorts_state(root, "abcDEF12345")["verified"] is False


def test_terminal_still_source_stops_rebuild_after_other_channels_handoff(tmp_path):
    root = tmp_path / "outputs/abcDEF12345-20260909"
    write(tmp_path / "outputs/cafe-publish-queue-20260823/queue.json",
          {"entries": [{"source_key": "abcDEF12345", "status": "pending"}]})
    write(root / "shorts/visual_validation.json", {"status": "rejected_still_source"})
    write(root / "cardnews/provider/publish_receipt.json",
          {"source_key": "abcDEF12345", "status": "published", "verified": True,
           "url": "https://youtube.com/post/x"})
    got = state.source_state(tmp_path, "abcDEF12345")
    assert got["terminal_blocked"] is True
    assert got["needs_production"] is False
    assert got["complete"] is False


def test_legacy_community_provider_evidence_is_protected_from_reposting(tmp_path):
    root = tmp_path / "outputs/abcDEF12345-20260909"
    write(root / "cardnews/provider/provider_scheduling_evidence.json",
          {"source_key": "abcDEF12345", "post": {
              "post_id": "Ugkx123", "post_url": "https://youtube.com/post/Ugkx123"}})
    got = state.community_state(root, "abcDEF12345")
    assert got["verified"] is False
    assert got["protected"] is True
    assert got["status"] == "legacy_provider_verify_only"


def test_scheduled_community_receipt_is_not_reported_as_published(tmp_path):
    root = tmp_path / "outputs/abcDEF12345-20260909"
    write(root / "cardnews/provider/publish_receipt.json",
          {"source_key": "abcDEF12345", "status": "scheduled", "verified": True,
           "url": "https://youtube.com/post/Ugkx123",
           "scheduled_at": "2026-09-26T20:00:00+09:00"})
    got = state.community_state(root, "abcDEF12345")
    assert got["status"] == "scheduled"
    assert got["verified"] is False
    assert got["provider_verified"] is True
    assert got["protected"] is True


def test_truncated_canonical_artifact_is_needs_review_not_overwrite_retry(tmp_path):
    root = tmp_path / "outputs/abcDEF12345-20260909"
    root.mkdir(parents=True)
    (root / "shorts").mkdir()
    (root / "shorts/final.mp4").write_bytes(b"partial")
    got = state.source_state(tmp_path, "abcDEF12345")
    assert got["needs_review"] == ["shorts/final.mp4: truncated"]
    assert got["needs_production"] is False
    assert got["complete"] is False


def test_verified_recovery_supersedes_preserved_render_failure(tmp_path):
    root = tmp_path / 'outputs/abcDEF12345-20260910'
    write(tmp_path / 'outputs/cafe-publish-queue-20260823/queue.json',
          {'entries': [{'source_key': 'abcDEF12345', 'status': 'pending'}]})
    write(root / 'shorts/visual_validation.json', {'status': 'rejected_still_source'})
    write(root / 'shorts/provider/journal.json', {
        'source_key': 'abcDEF12345', 'status': 'complete',
        'verified': {'shorts_url': 'https://youtube.com/shorts/verified', 'checks': {'source': True}}})
    write(root / 'cardnews/provider/publish_receipt.json', {
        'source_key': 'abcDEF12345', 'status': 'scheduled', 'verified': True,
        'url': 'https://youtube.com/post/verified', 'scheduled_at': '2099-01-03T20:00:00+09:00'})
    result = state.source_state(tmp_path, 'abcDEF12345')
    assert result['handed_off'] is True
    assert result['terminal_blocked'] is False
    assert result['complete'] is False
