from datetime import datetime
from types import SimpleNamespace

import pytest

import youtube_shorts_publisher as pub
from youtube_shorts_replacement import bind_old, text_hash, validate_contract


def fixture():
    description = 'https://youtu.be/pw8Bt97U6fk'
    slot = datetime.fromisoformat('2026-09-07T11:00:00+09:00')
    manifest = SimpleNamespace(source_key='pw8Bt97U6fk', canonical_urls=(description, 'https://www.youtube.com/watch?v=pw8Bt97U6fk'),
        replacement={'source_key': 'pw8Bt97U6fk', 'provider_id': '17gxjdEaTc8', 'title': '기존 제목',
                     'description_sha256': text_hash(description), 'scheduled_at': slot.isoformat()})
    old = SimpleNamespace(provider_id='17gxjdEaTc8', title='기존 제목', description=description,
                          status='scheduled', scheduled_at=slot)
    return manifest, old


def test_only_exact_predecessor_can_be_excluded_without_mutating_inventory():
    manifest, old = fixture()
    unrelated = SimpleNamespace(provider_id='anotherId01')
    inventory = SimpleNamespace(rows=(old, unrelated))
    assert bind_old(inventory, manifest) is old
    assert inventory.rows == (old, unrelated)
    old.description = 'https://youtu.be/anotherSrc1'
    with pytest.raises(pub.AmbiguousProviderState, match='source or old metadata'):
        bind_old(inventory, manifest)


def test_schedule_drift_and_duplicate_old_identity_are_rejected():
    manifest, old = fixture()
    with pytest.raises(pub.AmbiguousProviderState, match='single provider row'):
        bind_old(SimpleNamespace(rows=(old, old)), manifest)
    old.scheduled_at = datetime.fromisoformat('2026-09-08T11:00:00+09:00')
    with pytest.raises(pub.AmbiguousProviderState, match='schedule changed'):
        bind_old(SimpleNamespace(rows=(old,)), manifest)


def test_private_old_video_is_accepted_only_during_retirement_reconciliation():
    manifest, old = fixture()
    old.status, old.scheduled_at = 'private', None
    inventory = SimpleNamespace(rows=(old,))
    with pytest.raises(pub.AmbiguousProviderState):
        bind_old(inventory, manifest)
    assert bind_old(inventory, manifest, allow_private=True) is old
    assert validate_contract(manifest)[1].hour == 11
