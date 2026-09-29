import json
import subprocess
from datetime import datetime, timedelta, timezone

import cafe_queue_drain as drain


def setup_queue(tmp_path):
    path = tmp_path / drain.QUEUE_RELATIVE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'publication_mode': 'immediate_on_request', 'entries': [
        {'source_key': key, 'status': 'pending', 'attempts': 0} for key in ['a', 'b']]}))
    return path


def publisher(path, calls, first_fails=False):
    def run(argv, **kw):
        assert '--source-key' not in ' '.join(argv)
        assert kw['shell'] is False
        queue = json.loads(path.read_text())
        entry = next(e for e in queue['entries'] if e['status'] == 'pending')
        calls.append(entry['source_key'])
        entry['attempts'] += 1
        failed = first_fails and entry['source_key'] == 'a'
        if failed:
            entry.update(status='failed', next_eligible_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        else:
            entry.update(status='published', do_not_retry=True, published_url='https://cafe.naver.com/westudyssat/123')
        path.write_text(json.dumps(queue))
        return subprocess.CompletedProcess(argv, int(failed), stdout=json.dumps({
            'action': 'publish', 'source_key': entry['source_key'], 'ok': not failed}))
    return run


def test_two_published_sequentially_using_runner_order(tmp_path, monkeypatch):
    path = setup_queue(tmp_path)
    calls = []
    monkeypatch.setattr(drain.subprocess, 'run', publisher(path, calls))
    result = drain.drain(tmp_path)
    assert result['status'] == 'complete_for_now'
    assert calls == ['a', 'b']
    assert [e['status'] for e in result['attempted']] == ['published', 'published']


def test_true_prepublication_failure_yields_to_next_without_retry(tmp_path, monkeypatch):
    path = setup_queue(tmp_path)
    calls = []
    monkeypatch.setattr(drain.subprocess, 'run', publisher(path, calls, first_fails=True))
    result = drain.drain(tmp_path)
    assert result['status'] == 'complete_for_now'
    assert calls == ['a', 'b']
    assert [e['status'] for e in result['attempted']] == ['failed', 'published']


def test_live_guard_block_stops_without_another_runner(tmp_path, monkeypatch):
    setup_queue(tmp_path)
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({
            'action': 'skip', 'guard': {'status': 'blocked'}}))
    monkeypatch.setattr(drain.subprocess, 'run', run)
    result = drain.drain(tmp_path)
    assert result['reason'] == 'runner_guard_or_safety_stop'
    assert len(calls) == 1


def test_zero_exit_with_no_queue_progress_stops(tmp_path, monkeypatch):
    setup_queue(tmp_path)
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='already running')
    monkeypatch.setattr(drain.subprocess, 'run', run)
    assert drain.drain(tmp_path)['reason'] == 'no_queue_progress'
    assert len(calls) == 1


def test_uncertain_provider_state_stops_before_next_source(tmp_path, monkeypatch):
    path = setup_queue(tmp_path)
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        queue = json.loads(path.read_text())
        queue['entries'][0].update(status='reconcile_required', attempts=1, do_not_retry=True)
        path.write_text(json.dumps(queue))
        return subprocess.CompletedProcess(argv, 1, stdout=json.dumps({'action': 'publish', 'source_key': 'a'}))
    monkeypatch.setattr(drain.subprocess, 'run', run)
    assert drain.drain(tmp_path)['status'] == 'stopped'
    assert len(calls) == 1


def test_dry_run_has_no_provider_or_queue_mutation(tmp_path, monkeypatch):
    path = setup_queue(tmp_path)
    original = path.read_text()
    monkeypatch.setattr(drain.subprocess, 'run', lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))
    result = drain.drain(tmp_path, dry_run=True)
    assert result['eligible_source_keys'] == ['a', 'b']
    assert path.read_text() == original


def test_repeated_source_is_never_run_twice(tmp_path, monkeypatch):
    path = setup_queue(tmp_path)
    calls = []
    monkeypatch.setattr(drain.subprocess, 'run', publisher(path, calls, first_fails=True))
    monkeypatch.setattr(drain, 'eligible_entries', lambda project, queue, now: queue['entries'])
    result = drain.drain(tmp_path)
    assert result['reason'] == 'source_already_attempted'
    assert calls == ['a']


def test_interval_waits_in_slices_and_rechecks_queue_before_next_publish(tmp_path, monkeypatch):
    path = setup_queue(tmp_path)
    q = json.loads(path.read_text())
    q['success_interval_seconds'] = 600
    path.write_text(json.dumps(q))
    calls, waits = [], []
    publish = publisher(path, calls)
    def run(argv, **kw):
        result = publish(argv, **kw)
        current = json.loads(path.read_text())
        item = current['entries'][0]
        if calls == ['a']:
            item['provider_evidence'] = 'provider/13_provider_evidence.json'
            receipt = tmp_path / item['provider_evidence']
            receipt.parent.mkdir()
            receipt.write_text(json.dumps({'status': 'published_verified',
                'providerUrl': item['published_url'], 'verifiedAt': datetime.now(timezone.utc).isoformat()}))
            path.write_text(json.dumps(current))
        return result
    def sleep(seconds):
        assert calls == ['a']
        assert 0 < seconds <= 30
        waits.append(seconds)
        # Simulate elapsed wall-clock time through the persisted provider receipt.
        receipt = tmp_path / 'provider/13_provider_evidence.json'
        data = json.loads(receipt.read_text())
        data['verifiedAt'] = (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat()
        receipt.write_text(json.dumps(data))
    monkeypatch.setattr(drain.subprocess, 'run', run)
    monkeypatch.setattr(drain.time, 'sleep', sleep)
    assert drain.drain(tmp_path)['status'] == 'complete_for_now'
    assert calls == ['a', 'b']
    assert len(waits) == 1


def test_interval_wait_rechecks_uncertainty_without_running_provider(tmp_path, monkeypatch):
    path = setup_queue(tmp_path)
    q = json.loads(path.read_text())
    q['success_interval_seconds'] = 600
    q['entries'][0].update(status='published', do_not_retry=True,
        provider_evidence='provider/13_provider_evidence.json')
    path.write_text(json.dumps(q))
    receipt = tmp_path / 'provider/13_provider_evidence.json'
    receipt.parent.mkdir()
    receipt.write_text(json.dumps({'status': 'published_verified',
        'providerUrl': 'https://cafe.naver.com/westudyssat/123',
        'verifiedAt': datetime.now(timezone.utc).isoformat()}))
    def sleep(seconds):
        changed = json.loads(path.read_text())
        changed['entries'][1]['status'] = 'reconcile_required'
        path.write_text(json.dumps(changed))
    monkeypatch.setattr(drain.time, 'sleep', sleep)
    monkeypatch.setattr(drain.subprocess, 'run', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('provider must not run')))
    result = drain.drain(tmp_path)
    assert result['reason'] == 'reconcile_required:b'
    assert result['attempted'] == []
