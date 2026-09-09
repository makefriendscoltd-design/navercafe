import json
from pathlib import Path

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
