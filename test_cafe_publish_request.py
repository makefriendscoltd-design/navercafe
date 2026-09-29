import json
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import cafe_publish_request as request
import cafe_queue_enroll as enroll
import reference_daily_production as daily


def queue_file(tmp_path, **entry):
    path = tmp_path / request.QUEUE_RELATIVE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'publication_mode': 'immediate_on_request', 'entries': [
        {'source_key': 'key', 'status': 'pending', **entry}]}))
    return path


def test_request_uses_locked_runner_cli_and_never_calls_skip_published(tmp_path, monkeypatch):
    queue_file(tmp_path)
    calls = []
    monkeypatch.setattr(request.subprocess, 'run', lambda command, **kw:
                        calls.append((command, kw)) or subprocess.CompletedProcess(command, 0))
    result = request.publish_enrolled('key', project=tmp_path)
    assert result['status'] == 'deferred'
    assert result['published'] is False
    assert calls[0][0][-1] == '--source-key=key'
    assert calls[0][1]['shell'] is False
    assert len(calls) == 1


def test_request_reports_provider_success_from_updated_queue(tmp_path, monkeypatch):
    path = queue_file(tmp_path)
    def run(command, **kw):
        q = json.loads(path.read_text())
        q['entries'][0].update(status='published', published_url='https://cafe.naver.com/westudyssat/6238')
        path.write_text(json.dumps(q))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(request.subprocess, 'run', run)
    result = request.publish_enrolled('key', project=tmp_path)
    assert result['status'] == 'published'
    assert result['provider_url'].endswith('/6238')


def test_request_does_not_invent_unenrolled_source(tmp_path, monkeypatch):
    queue_file(tmp_path)
    monkeypatch.setattr(request.subprocess, 'run', lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))
    assert request.publish_enrolled('missing', project=tmp_path)['status'] == 'not_enrolled'


def test_timeout_preserves_queue_for_provider_reconciliation(tmp_path, monkeypatch):
    path = queue_file(tmp_path, status='reconcile_required', do_not_retry=True)
    original = path.read_text()
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], 10)
    monkeypatch.setattr(request.subprocess, 'run', timeout)
    result = request.publish_enrolled('key', project=tmp_path)
    assert result['status'] == 'deferred'
    assert result['runner_exit_code'] is None
    assert path.read_text() == original


def test_immediate_enrollment_has_no_calendar_requirement():
    now = datetime(2026, 9, 29, 2, 5, tzinfo=ZoneInfo('Asia/Seoul'))
    assert enroll.next_slot(now, {'publication_mode': 'immediate_on_request', 'entries': []}) == now


def test_request_enrolls_then_dispatches_exactly_once(tmp_path, monkeypatch):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'source_key': 'key'}))
    calls = []
    monkeypatch.setattr(enroll, 'enroll', lambda p: calls.append('enroll') or
                        {'status': 'already_enrolled', 'source_key': 'key'})
    monkeypatch.setattr(request, 'publish_enrolled', lambda key:
                        calls.append(key) or {'status': 'deferred', 'published': False})
    assert request.request(manifest)['status'] == 'deferred'
    assert calls == ['enroll', 'key']


def test_production_requests_cafe_even_when_already_enrolled(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, 'PROJECT', tmp_path)
    calls = []
    monkeypatch.setattr(daily, '_run', lambda args, **kw: calls.append(args) or
                        (False, '{"status":"deferred","published":false}'))
    result = daily.publish(tmp_path / 'key-20260929', {'channels': {
        'source': {'status': 'pass'}, 'cafe': {'status': 'pass'},
        'cardnews': {'status': 'fail'}, 'shorts': {'status': 'fail'}}})
    assert result['cafe_publish'] == 'deferred'
    assert len(calls) == 1 and calls[0][0] == 'cafe_publish_request.py'


def test_normal_enroll_cli_dispatches_only_once_and_reports_deferred(monkeypatch):
    calls = []
    monkeypatch.setattr(enroll, 'enroll', lambda *a, **kw:
                        {'status': 'already_enrolled', 'source_key': 'key'})
    monkeypatch.setattr(request, 'publish_enrolled', lambda key:
                        calls.append(key) or {'status': 'deferred', 'published': False})
    assert enroll.main(['--manifest', 'unused.json']) == 2
    assert calls == ['key']
    calls.clear()
    assert enroll.main(['--manifest', 'unused.json', '--enqueue-only']) == 0
    assert calls == []


def test_immediate_production_default_uncapped_but_explicit_limit_honored():
    queue = {'publication_mode': 'immediate_on_request'}
    assert daily.cafe_production_limit(queue, 20, None) == 20
    assert daily.cafe_production_limit(queue, 20, 2) == 2
    assert daily.cafe_production_limit({}, 20, None) == daily.CAFE_DAILY_LIMIT
