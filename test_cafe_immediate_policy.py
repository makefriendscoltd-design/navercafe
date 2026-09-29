import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import cafe_publication_policy as policy
import cafe_queue_runner as runner
import content_queue_guard as guard
import cafe_manifest_publisher as publisher

NOW = datetime(2026, 9, 29, 2, 15, tzinfo=timezone(timedelta(hours=9)))


def queue():
    return {'publication_mode': 'immediate_on_request', 'timezone': 'Asia/Seoul',
            'windows': ['10:00'], 'entries': [
                {'source_key': 'a', 'status': 'pending', 'attempts': 0,
                 'not_before': '2099-01-01T00:00:00+09:00',
                 'planned_publish_at': '2099-01-01T00:00:00+09:00'},
                {'source_key': 'b', 'status': 'pending', 'attempts': 0},
            ]}


def test_request_ignores_calendar_and_missing_shorts_without_mutation(tmp_path):
    q = queue()
    before = copy.deepcopy(q)
    assert [e['source_key'] for e in policy.eligible_entries(tmp_path, q, NOW)] == ['a', 'b']
    assert runner.rate_block(q, NOW) is None
    assert policy.publication_block(tmp_path, q, NOW, 'b') is None
    assert q == before


def test_target_request_never_substitutes_a_different_source(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, 'PROJECT', tmp_path)
    q = queue()
    assert guard.select_entry(q, NOW, 'b')['source_key'] == 'b'
    q['entries'][1]['status'] = 'blocked'
    assert guard.select_entry(q, NOW, 'b') is None
    assert guard.select_entry(q, NOW, 'missing') is None


def test_retry_and_do_not_retry_still_apply(tmp_path):
    q = queue()
    q['entries'][0].update(status='failed', attempts=1,
                           next_eligible_at=(NOW + timedelta(minutes=1)).isoformat())
    q['entries'][1]['do_not_retry'] = True
    assert policy.eligible_entries(tmp_path, q, NOW) == []
    q['entries'][0]['next_eligible_at'] = NOW.isoformat()
    assert len(policy.eligible_entries(tmp_path, q, NOW)) == 1


def test_uncertain_other_entry_blocks_even_targeted_request(tmp_path):
    q = queue()
    q['entries'][0]['status'] = 'reconcile_required'
    assert policy.publication_block(tmp_path, q, NOW, 'b') == 'reconcile_required:a'
    q['entries'][0]['status'] = 'failed'
    q['entries'][0]['provider_evidence'] = 'provider/13_provider_evidence.json'
    (tmp_path / 'provider').mkdir()
    (tmp_path / 'provider/12_provider_success_reservation.json').write_text('{}')
    assert policy.publication_block(tmp_path, q, NOW, 'b') == 'reconcile_required:a'


def test_publisher_rechecks_immediate_queue_and_does_not_read_shorts(tmp_path, monkeypatch):
    q = queue()
    monkeypatch.setattr(publisher, 'PROJECT', tmp_path)
    monkeypatch.setattr(publisher, 'read_json', lambda *_: q)
    publisher.enforce_cafe_publish_window(NOW, source_key='b')
    q['entries'][1]['status'] = 'published'
    import pytest
    with pytest.raises(publisher.CafePublishWindowClosed):
        publisher.enforce_cafe_publish_window(NOW, source_key='b')


def test_live_guard_immediate_never_refreshes_inventory(tmp_path, monkeypatch):
    q = queue()
    q['entries'][1].update(publisher_command='test', manifest='m')
    path = tmp_path / 'queue.json'
    path.write_text(json.dumps(q))
    monkeypatch.setattr(guard, 'PROJECT', tmp_path)
    monkeypatch.setattr(guard, 'QUEUE', path)
    monkeypatch.setattr(guard, 'check_live_prompt', lambda: None)
    refresh = Mock(side_effect=AssertionError('Shorts must not be queried'))
    monkeypatch.setattr(guard, 'refresh_shorts_inventory', refresh)
    monkeypatch.setattr(publisher, 'resolve_manifest', lambda _: (Path('m'), None, None, None))
    monkeypatch.setattr(publisher, 'enforce_cafe_publish_window', lambda *a, **k: None)
    monkeypatch.setattr(publisher, 'validate_cafe_eligibility', lambda *a: {'status': 'pass', 'failures': []})
    assert guard.audit(live=True, source_key='b')['source_key'] == 'b'
    refresh.assert_not_called()


def test_variable_quotes_require_bound_v2_instruction(tmp_path):
    from cafe_caption_source import prompt_hash
    manifest_path = tmp_path / 'manifest.json'
    receipt = tmp_path / 'evidence.json'
    manifest = {'manuscript_source': 'captions', 'manuscript_evidence': receipt.name,
                'expected_quotes': 6, 'expected_quote_texts': [str(i) for i in range(6)]}
    assert not publisher.quote_contract(manifest_path, manifest)
    receipt.write_text(json.dumps({'instruction_version': 'cafe-business-column/v2',
                                   'instruction_sha256': prompt_hash('cafe-business-column/v2')}))
    assert publisher.quote_contract(manifest_path, manifest)
    receipt.write_text(json.dumps({'instruction_version': 'cafe-caption/v1',
                                   'instruction_sha256': prompt_hash('cafe-caption/v1')}))
    assert not publisher.quote_contract(manifest_path, manifest)
