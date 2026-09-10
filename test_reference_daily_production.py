import json
import subprocess

import reference_daily_production as daily
import content_run_state as state


def test_source_measurement_failure_is_preserved_and_never_published(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unverified source must not publish")
    monkeypatch.setattr(daily, '_run', forbidden)
    problem = '원본 측정 실패: Requested format is not available'
    result = daily.publish(tmp_path / 'abcdefghijk-20260910', {
        'channels': {'source': {'status': 'fail', 'problems': [problem]}}})
    assert result == {'source': 'fail: ' + problem}


def test_channel_failure_does_not_block_other_provider(tmp_path, monkeypatch):
    root = tmp_path / 'outputs/abcdefghijk-20260910'
    (root / 'shorts').mkdir(parents=True)
    (root / 'shorts/07_provider_manifest.json').write_text('{}')
    monkeypatch.setattr(daily, 'PROJECT', tmp_path)
    calls = []
    monkeypatch.setattr(daily, '_run', lambda args, **kw: (calls.append(args) or (True, 'ok')))
    verdict = {'status': 'fail', 'channels': {
        'source': {'status': 'pass'}, 'cafe': {'status': 'fail'},
        'cardnews': {'status': 'fail'}, 'shorts': {'status': 'pass'}}}
    result = daily.publish(root, verdict)
    assert result['shorts_publish'] == 'ok'
    assert any(args[0] == 'youtube_shorts_aside_adapter.py' for args in calls)


def test_timeout_is_a_channel_failure_not_an_exception(monkeypatch):
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired('child', 42)
    monkeypatch.setattr(daily.subprocess, 'run', timeout)
    ok, detail = daily._run(['x.py'])
    assert not ok and '42' in detail


def test_successful_handoff_is_not_reported_as_a_failed_job(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, 'PROJECT', tmp_path)
    monkeypatch.setattr(daily, 'REPORT_DIR', tmp_path / 'reports')
    monkeypatch.setattr(daily.selection, 'SEEN_PATH', tmp_path / 'seen.json')
    monkeypatch.setattr(daily.selection, 'QUEUE_PATH', tmp_path / 'queue.json')
    monkeypatch.setattr(daily.selection, 'load_candidates', lambda: [{'id': 'abcdefghijk', 'title': 'T'}])
    monkeypatch.setattr(daily.selection, 'select', lambda c, limit: (c, []))
    monkeypatch.setattr(daily, 'produce', lambda *a: {
        'source_key': 'abcdefghijk', 'complete': False, 'handed_off': True})
    assert daily.main(['--limit', '1']) == 0
    assert daily.main(['--limit', '1']) == 0
    reports = list((tmp_path / 'reports').glob('*.json'))
    assert len(reports) == 2
    assert len((tmp_path / 'reports/runs.jsonl').read_text().splitlines()) == 2
    assert all(not json.loads(p.read_text())['results'][0]['complete'] for p in reports)
