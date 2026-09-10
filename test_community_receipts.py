import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import youtube_community_auto as community


KEY = 'YAsxyoTWFDA'
TEXT = 'original body https://youtu.be/' + KEY


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    images = []
    for i in range(10):
        p = tmp_path / f'{i}.png'
        p.write_bytes(bytes([i]))
        images.append(str(p))
    monkeypatch.setattr(community, 'track_publication', lambda *a: True)
    return dict(text=TEXT, images=images, source_key=KEY,
                receipt=tmp_path / 'provider/receipt.json', expected_channel='나민수 AI')


def verified():
    return {'status': 'verified', 'provider': {'verified': True,
            'url': 'https://www.youtube.com/post/UgKnown', 'images': 10}}


def test_existing_provider_post_is_reconciled_without_click(inputs, monkeypatch):
    monkeypatch.setattr(community, 'inspect_posts', lambda *a, **kw: verified())
    monkeypatch.setattr(community, 'post_to_youtube_community', lambda *a, **k: pytest.fail('duplicate'))
    assert community.publish_verified(**inputs)['verified']
    assert json.loads(inputs['receipt'].read_text())['crm_status'] == 'historical_observation_no_new_send'


def test_interrupted_publish_cannot_be_repeated(inputs, monkeypatch):
    monkeypatch.setattr(community, 'inspect_posts', lambda *a, **kw:
                        {'status': 'not_verified', 'related_count': 0, 'exhausted': True})
    calls = []
    def interrupt(*a, **kw):
        calls.append(1)
        raise TimeoutError('provider outcome unknown')
    monkeypatch.setattr(community, 'post_to_youtube_community', interrupt)
    with pytest.raises(TimeoutError):
        community.publish_verified(**inputs)
    with pytest.raises(RuntimeError, match='ambiguous'):
        community.publish_verified(**inputs)
    assert len(calls) == 1


def test_toast_without_provider_identity_is_not_success(inputs, monkeypatch):
    monkeypatch.setattr(community, 'inspect_posts', lambda *a, **kw:
                        {'status': 'not_verified', 'related_count': 0, 'exhausted': True})
    monkeypatch.setattr(community, 'post_to_youtube_community', lambda *a, **k: {'status': 'published'})
    with pytest.raises(RuntimeError, match='verification failed'):
        community.publish_verified(**inputs)
    assert json.loads(inputs['receipt'].read_text())['status'] == 'reserved_verify_only'


def test_incomplete_inventory_refuses_new_publish(inputs, monkeypatch):
    monkeypatch.setattr(community, 'inspect_posts', lambda *a, **kw:
                        {'status': 'not_verified', 'related_count': 0, 'exhausted': False})
    monkeypatch.setattr(community, 'post_to_youtube_community', lambda *a, **k: pytest.fail('unsafe'))
    with pytest.raises(RuntimeError, match='ambiguous'):
        community.publish_verified(**inputs)


def test_verified_receipt_rejects_changed_body(inputs, monkeypatch):
    monkeypatch.setattr(community, 'inspect_posts', lambda *a, **kw: verified())
    community.publish_verified(**inputs)
    inputs['text'] += ' modified'
    with pytest.raises(RuntimeError, match='different content'):
        community.publish_verified(**inputs)


def test_verify_only_never_tracks_or_posts(inputs, monkeypatch):
    monkeypatch.setattr(community, 'inspect_posts', lambda *a, **kw: verified())
    monkeypatch.setattr(community, 'track_publication', lambda *a: pytest.fail('tracking'))
    monkeypatch.setattr(community, 'post_to_youtube_community', lambda *a, **kw: pytest.fail('posting'))
    assert community.publish_verified(**inputs, verify_only=True)['verified']
    assert not inputs['receipt'].exists()


def test_legacy_scheduled_post_absent_from_public_feed_is_never_reposted(inputs, monkeypatch):
    inputs['receipt'].parent.mkdir(parents=True)
    (inputs['receipt'].parent / '02_provider_reverify.json').write_text(json.dumps({
        'status': 'scheduled', 'url': 'https://www.youtube.com/post/UgOld'}))
    monkeypatch.setattr(community, 'inspect_posts', lambda *a, **kw:
                        {'status': 'not_verified', 'related_count': 0, 'exhausted': True})
    monkeypatch.setattr(community, 'post_to_youtube_community', lambda *a, **k: pytest.fail('duplicate'))
    with pytest.raises(RuntimeError, match='ambiguous'):
        community.publish_verified(**inputs)


def test_schedule_records_distinct_verified_state_and_planned_crm(inputs, monkeypatch, tmp_path):
    slot = datetime(2099, 1, 3, 20, 0, tzinfo=ZoneInfo('Asia/Seoul'))
    monkeypatch.setattr(community, 'PROVIDER_LOCK', tmp_path / 'provider.lock')
    monkeypatch.setattr(community, '_schedule_inventory', lambda **kw: {
        'scheduled': {'rows': []}, 'public': {'rows': []}})
    monkeypatch.setattr(community, '_schedule_provider', lambda *a, **kw: {
        'status': 'scheduled_verified', 'provider_schedule_click_count': 1,
        'post_id': 'UgScheduled', 'url': 'https://www.youtube.com/post/UgScheduled',
        'images': 10, 'provider_status_text': '2099. 1. 3. 20:00 예정(현지 시간)',
        'page_reloaded': True})
    tracked = []
    monkeypatch.setattr(community, 'track_external_event',
                        lambda channel, key, stage: tracked.append((channel, key, stage)) or True)
    result = community.schedule_verified(**inputs,
        community_url='https://www.youtube.com/channel/UCExample/posts', schedule_at=slot)
    assert result['status'] == 'scheduled'
    assert result['verified'] is True
    assert result['scheduled_at'] == slot.isoformat()
    assert tracked == [('youtube_community', KEY, 'planned')]
    assert json.loads(inputs['receipt'].read_text())['status'] == 'scheduled'


def test_schedule_never_retries_unresolved_provider_attempt(inputs, monkeypatch, tmp_path):
    slot = datetime(2099, 1, 3, 20, 0, tzinfo=ZoneInfo('Asia/Seoul'))
    monkeypatch.setattr(community, 'PROVIDER_LOCK', tmp_path / 'provider.lock')
    inputs['receipt'].parent.mkdir(parents=True, exist_ok=True)
    identity = {
        'source_key': KEY,
        'text_sha256': community.hashlib.sha256(TEXT.encode()).hexdigest(),
        'images_sha256': [community.hashlib.sha256(Path(p).read_bytes()).hexdigest()
                          for p in inputs['images']],
        'status': 'reserved_schedule_verify_only',
    }
    inputs['receipt'].write_text(json.dumps(identity))
    monkeypatch.setattr(community, '_schedule_inventory',
                        lambda **kw: pytest.fail('must not inspect or retry'))
    with pytest.raises(RuntimeError, match='unresolved provider attempt'):
        community.schedule_verified(**inputs,
            community_url='https://www.youtube.com/channel/UCExample/posts', schedule_at=slot)


def test_schedule_reconciliation_locks_state_and_retries_only_failed_tracking(inputs, monkeypatch, tmp_path):
    import fcntl
    import hashlib

    receipt = inputs['receipt']
    receipt.parent.mkdir()
    receipt.write_text(json.dumps({
        'source_key': KEY, 'text_sha256': hashlib.sha256(TEXT.encode()).hexdigest(),
        'images_sha256': [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in inputs['images']],
        'status': 'reserved_schedule_verify_only', 'provider_schedule_click_count': 1,
        'scheduled_at': '2099-01-03T20:00:00+09:00'}))
    lock_path = tmp_path / 'provider.lock'
    monkeypatch.setattr(community, 'PROVIDER_LOCK', lock_path)
    scans = []

    def inventory(**kwargs):
        for path in (lock_path, receipt.with_suffix('.lock')):
            with path.open('a') as handle:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        scans.append(1)
        return {'public': {'rows': []}, 'scheduled': {'exhausted': True, 'rows': [{
            'text': TEXT, 'post_id': 'UgScheduled', 'image_count': 10,
            'time': '2099. 1. 3. 20:00 예정(현지 시간)'}]}}

    monkeypatch.setattr(community, '_schedule_inventory', inventory)
    monkeypatch.setattr(community, '_schedule_provider', lambda *a, **kw: pytest.fail('duplicate schedule'))
    attempts = []

    def track(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError('tracking unavailable')
        return True

    monkeypatch.setattr(community, 'track_external_event', track)
    kwargs = dict(inputs, community_url='https://www.youtube.com/channel/UC123/posts')
    with pytest.raises(RuntimeError, match='tracking unavailable'):
        community.reconcile_scheduled(**kwargs)
    saved = json.loads(receipt.read_text())
    assert saved['status'] == 'scheduled' and saved['fresh_inventory_query'] is True
    assert 'page_reloaded' not in saved
    assert community.reconcile_scheduled(**kwargs)['crm_tracked'] is True
    assert scans == [1] and attempts == [1, 1]
