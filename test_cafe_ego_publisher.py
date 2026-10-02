"""Provider stage failures must preserve the one-submit safety barrier."""
import json
from pathlib import Path

import pytest
import cafe_ego_publisher as ego
import cafe_manifest_publisher as canonical


@pytest.fixture
def provider(tmp_path, monkeypatch):
    evidence = tmp_path / 'evidence'
    data = {'source_key': 'source', 'title': 'title', 'category': 'category', 'quotes': ['q']}
    monkeypatch.setattr(ego, 'LOCK', tmp_path / 'provider.lock')
    monkeypatch.setattr(ego, 'make_input', lambda _: data)
    monkeypatch.setattr(canonical, 'validate_cafe_eligibility', lambda *a: {'status': 'pass'})
    monkeypatch.setattr(canonical, 'enforce_cafe_publish_window', lambda **kw: None)
    calls = []
    def stage(name, data, **kw):
        calls.append(name)
        if name == 'precheck':
            return {'status': 'ok', 'duplicateCount': 0}
        if name == 'publish':
            assert (evidence / 'provider_uncertain_do_not_retry.json').is_file()
            return {'status': 'published', 'url': 'https://cafe.naver.com/westudyssat/123'}
        if name == 'verify_public':
            return {'status': 'verified', 'oglinks': 1, 'embeds': 1}
        return {'status': 'pass'}
    monkeypatch.setattr(ego, 'browser', stage)
    def crm(*a):
        assert 'verify_public' in calls
        assert (evidence / '13_provider_evidence.json').is_file()
        calls.append('crm')
        return {'exitCode': 0}
    monkeypatch.setattr(canonical, 'crm_emit', crm)
    def run(**kw):
        return ego.publish(tmp_path / 'manifest.json', tmp_path, tmp_path / 'provider', evidence, **kw)
    return evidence, calls, stage, run


def test_prepare_only_never_submits_or_tracks(provider):
    evidence, calls, stage, run = provider
    run(prepare_only=True)
    assert calls == ['precheck', 'fill']
    assert not (evidence / 'provider_uncertain_do_not_retry.json').exists()
    assert not (evidence / '13_provider_evidence.json').exists()


def test_success_tracks_only_after_public_verification_and_refuses_republish(provider):
    evidence, calls, stage, run = provider
    run()
    assert calls == ['precheck', 'fill', 'precheck', 'verify_editor', 'publish', 'verify_public', 'crm']
    receipt = json.loads((evidence / '13_provider_evidence.json').read_text())
    assert receipt['crm']['status'] == 'success'
    with pytest.raises(RuntimeError, match='reconcile'):
        run()
    assert calls.count('publish') == 1
    assert calls.count('crm') == 1


@pytest.mark.parametrize('failure_stage', ['publish', 'verify_public'])
def test_ambiguous_postsubmit_failure_persists_barrier_and_blocks_retry(provider, monkeypatch, failure_stage):
    evidence, calls, stage, run = provider
    def browser(name, data, **kw):
        if name == failure_stage:
            calls.append(name)
            raise TimeoutError('provider connection lost')
        return stage(name, data, **kw)
    monkeypatch.setattr(ego, 'browser', browser)
    with pytest.raises(TimeoutError):
        run()
    assert (evidence / 'provider_uncertain_do_not_retry.json').is_file()
    assert 'crm' not in calls
    previous = list(calls)
    with pytest.raises(RuntimeError, match='reconcile'):
        run()
    assert calls == previous


def test_duplicate_during_final_precheck_prevents_submit(provider, monkeypatch):
    evidence, calls, stage, run = provider
    def browser(name, data, **kw):
        if name == 'precheck' and 'fill' in calls:
            calls.append(name)
            return {'status': 'duplicate', 'duplicateCount': 1}
        return stage(name, data, **kw)
    monkeypatch.setattr(ego, 'browser', browser)
    with pytest.raises(RuntimeError, match='duplicate'):
        run()
    assert 'publish' not in calls
    assert 'crm' not in calls


def test_crm_failure_never_allows_a_second_provider_submission(provider, monkeypatch):
    evidence, calls, stage, run = provider
    monkeypatch.setattr(canonical, 'crm_emit', lambda *a: {'exitCode': 1})
    with pytest.raises(RuntimeError, match='crm_failed'):
        run()
    assert (evidence / '13_provider_evidence.json').is_file()
    with pytest.raises(RuntimeError, match='reconcile'):
        run()
    assert calls.count('publish') == 1


def test_tracking_recovery_twice_tracks_once_without_browser(provider, monkeypatch, tmp_path):
    evidence, calls, stage, run = provider
    monkeypatch.setattr(canonical, 'crm_emit', lambda *a: {'exitCode': 1})
    with pytest.raises(RuntimeError, match='crm_failed'):
        run()
    q = {'entries': [{'source_key': 'source', 'provider_evidence': str(evidence / '13_provider_evidence.json')}]}
    tracking = []
    monkeypatch.setattr(canonical, 'crm_emit', lambda *args: (tracking.append(args) or {'exitCode': 0}))
    monkeypatch.setattr(ego, 'browser', lambda *a, **kw: pytest.fail('tracking recovery must not browse'))
    assert ego.recover_pending_tracking(tmp_path, q)
    assert ego.recover_pending_tracking(tmp_path, q)
    assert len(tracking) == 1
    assert not (evidence / 'ego_crm_pending.json').exists()
    assert calls.count('publish') == 1


@pytest.mark.parametrize('status', ['published', 'observed', 'provider_success_reserved'])
def test_tracking_recovery_skips_unverified_receipt(tmp_path, monkeypatch, status):
    receipt = tmp_path / '13_provider_evidence.json'
    receipt.write_text(json.dumps({'status': status, 'providerBackend': 'ego',
        'sourceKey': 'source', 'providerUrl': 'https://cafe.naver.com/westudyssat/123'}))
    monkeypatch.setattr(ego, 'LOCK', tmp_path / 'provider.lock')
    monkeypatch.setattr(canonical, 'crm_emit', lambda *a: pytest.fail('unverified publication must not track'))
    assert ego.recover_pending_tracking(tmp_path, {'entries': [
        {'source_key': 'source', 'provider_evidence': str(receipt)}]})


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_browser_accepts_unique_result_from_either_stream_and_injects_payload(tmp_path, monkeypatch, stream):
    import subprocess
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'space.json').write_text(json.dumps({'spaceId': 's', 'checkPage': 'check', 'page': 'editor'}))
    (tmp_path / 'cafe_ego_browser.mjs').write_text('const payload=__CAFE_EGO_PAYLOAD__;')
    monkeypatch.setattr(ego, 'PROJECT', tmp_path)
    monkeypatch.setattr(ego, 'RUNTIME', runtime)
    monkeypatch.setattr(ego.shutil, 'which', lambda _: '/bin/ego-browser')
    def run(argv, **kw):
        assert argv == ['/bin/ego-browser', 'nodejs']
        assert '__CAFE_EGO_PAYLOAD__' not in kw['input']
        assert 'env' not in kw
        payload = json.loads(kw['input'].removeprefix('const payload=').removesuffix(';'))
        assert payload['input']['title'] == '한글 test'
        assert payload['stage'] == 'precheck'
        output = {'stdout': '', 'stderr': ''}
        output[stream] = 'CAFE_EGO_RESULT {"status":"ok"}\n'
        return subprocess.CompletedProcess(argv, 0, **output)
    monkeypatch.setattr(ego.subprocess, 'run', run)
    assert ego.browser('precheck', {'title': '한글 test'}) == {'status': 'ok'}


@pytest.mark.parametrize('verification,url', [
    ({'status': 'observed', 'oglinks': 1, 'embeds': 1}, 'https://cafe.naver.com/westudyssat/123'),
    ({'status': 'verified', 'oglinks': 0, 'embeds': 1}, 'https://cafe.naver.com/westudyssat/123'),
    ({'status': 'verified', 'oglinks': 1, 'embeds': 0}, 'https://cafe.naver.com/westudyssat/123'),
    ({'status': 'verified', 'oglinks': 1, 'embeds': 1}, 'https://example.com/123'),
])
def test_tracking_recovery_fails_closed_on_invalid_public_evidence(tmp_path, monkeypatch, verification, url):
    receipt = tmp_path / '13_provider_evidence.json'
    receipt.write_text(json.dumps({'status': 'published_verified', 'providerBackend': 'ego',
        'sourceKey': 'source', 'providerUrl': url, 'publicVerification': verification}))
    monkeypatch.setattr(ego, 'LOCK', tmp_path / 'provider.lock')
    monkeypatch.setattr(canonical, 'crm_emit', lambda *a: pytest.fail('invalid evidence must not track'))
    assert ego.recover_pending_tracking(tmp_path, {'entries': [
        {'source_key': 'source', 'provider_evidence': str(receipt)}]}) is False
