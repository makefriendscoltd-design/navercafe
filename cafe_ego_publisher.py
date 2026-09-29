"""Ego Lite provider adapter; queue gates and success receipts remain canonical."""
from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from datetime import datetime

PROJECT = Path(__file__).resolve().parent
RUNTIME = PROJECT / 'outputs/cafe-ego-runtime'
LOCK = Path('/tmp/aimax-ego-cafe-provider.lock')


def tokens_for(body: str) -> list[dict]:
    result = []
    for token in re.split(r'(\[BLOCKQUOTE\][\s\S]*?\[/BLOCKQUOTE\]|\[IMAGE_HERE\])', body):
        if token == '[IMAGE_HERE]':
            result.append({'kind': 'image'})
        elif token.startswith('[BLOCKQUOTE]'):
            result.append({'kind': 'quote', 'text': token[len('[BLOCKQUOTE]'):-len('[/BLOCKQUOTE]')].strip()})
        else:
            text = re.sub(r'\[/?(?:BOLD|HIGHLIGHT)\]', '', token).strip()
            if text:
                result.append({'kind': 'text', 'text': text})
    if sum(t['kind'] == 'image' for t in result) != 5:
        raise ValueError('Expected five body image markers')
    return result


def make_input(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    body = (manifest_path.parent / manifest['body_file']).read_text()
    tokens = tokens_for(body)
    return {**manifest, 'tokens': tokens, 'sequence': [t['kind'] for t in tokens],
            'quotes': [t['text'] for t in tokens if t['kind'] == 'quote'],
            'texts': [t['text'] for t in tokens if t['kind'] == 'text'],
            'images': [str((manifest_path.parent / p).resolve()) for p in manifest['images']]}


def browser(stage: str, data: dict, **kwargs) -> dict:
    space = json.loads((RUNTIME / 'space.json').read_text())
    if not space.get('spaceId') or not space.get('checkPage'):
        raise RuntimeError('Ego runtime space must be initialized explicitly')
    command = shutil.which('ego-browser')
    if not command:
        raise RuntimeError('ego-browser is not installed/onboarded')
    script = (PROJECT / 'cafe_ego_browser.mjs').read_text().replace('__CAFE_EGO_PAYLOAD__', json.dumps({'stage': stage, 'space': space, 'input': data, 'helpersUrl': (PROJECT / 'cafe_ego_url.mjs').as_uri(), **kwargs}, ensure_ascii=False))
    proc = subprocess.run([command, 'nodejs'], input=script,
                          text=True, capture_output=True, timeout=900 if stage == 'fill' else 180)
    if proc.returncode:
        raise RuntimeError(f'Ego {stage} failed: {(proc.stderr or proc.stdout)[-1800:]}')
    results = [line.removeprefix('CAFE_EGO_RESULT ') for line in (proc.stdout + '\n' + proc.stderr).splitlines() if line.startswith('CAFE_EGO_RESULT ')]
    if len(results) != 1:
        (RUNTIME / ('last-' + stage + '-output.txt')).write_text(proc.stdout + '\n' + proc.stderr)
        raise RuntimeError(f'Ego {stage} missing unique result; raw result stored in runtime')
    return json.loads(results[0])


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def publish(manifest_path: Path, base: Path, provider: Path, evidence: Path, *, prepare_only=False):
    import cafe_manifest_publisher as canonical
    gate = canonical.validate_cafe_eligibility(manifest_path, provider, evidence)
    if gate['status'] != 'pass':
        raise RuntimeError({'cafe_eligibility_gate_failed': gate})
    data = make_input(manifest_path)
    evidence.mkdir(parents=True, exist_ok=True)
    barrier = evidence / 'provider_uncertain_do_not_retry.json'
    with LOCK.open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        for name in ('13_provider_evidence.json', '12_provider_success_reservation.json',
                     'provider_uncertain_do_not_retry.json', 'published_but_verification_failed_do_not_retry.json'):
            if (evidence / name).exists():
                raise RuntimeError(f'Existing provider evidence: {name}; reconcile before retry')
        canonical.enforce_cafe_publish_window(source_key=data['source_key'])
        precheck = browser('precheck', data)
        write(evidence / 'ego_precheck.json', precheck)
        if precheck['status'] != 'ok' or precheck.get('duplicateCount') != 0:
            raise RuntimeError({'cafe_precommit_duplicate': precheck})
        draft = browser('fill', data, screenshot=str(evidence / 'ego_editor.png'))
        write(evidence / 'ego_editor.json', draft)
        if draft['status'] != 'pass':
            raise RuntimeError({'ego_editor_validation_failed': draft['checks']})
        if prepare_only:
            print(json.dumps({'status': 'editor_verified_unsaved', 'sourceKey': data['source_key']}))
            return
        # Recheck duplicates in a separate page and canonical cooldown immediately before submit.
        precheck = browser('precheck', data)
        write(evidence / 'ego_precommit.json', precheck)
        if precheck['status'] != 'ok' or precheck.get('duplicateCount') != 0:
            raise RuntimeError({'cafe_precommit_duplicate': precheck})
        canonical.enforce_cafe_publish_window(source_key=data['source_key'])
        fresh = browser('verify_editor', data)
        if fresh['status'] != 'pass':
            raise RuntimeError('Ego editor changed before submission')
        write(barrier, {'status': 'uncertain_do_not_retry', 'sourceKey': data['source_key'],
                        'providerBackend': 'ego', 'stage': 'before_submit', 'at': datetime.now().astimezone().isoformat()})
        result = browser('publish', data)
        write(evidence / 'ego_submit.json', result)
        url, article_id = canonical.canonical_cafe_article_url(result.get('url', ''))
        verified = browser('verify_public', data,
                           url=f'https://cafe.naver.com/ca-fe/cafes/26321967/articles/{article_id}',
                           screenshot=str(evidence / 'public_verification.png'))
        write(evidence / 'ego_public_verification.json', verified)
        if verified['status'] != 'verified':
            raise RuntimeError({'provider_publication_unverified': verified['checks']})
        stamp = datetime.now().astimezone().isoformat(timespec='seconds')
        write(evidence / '12_provider_success_reservation.json', {'status': 'provider_success_reserved',
              'sourceKey': data['source_key'], 'providerUrl': url, 'articleId': article_id, 'verifiedAt': stamp})
        receipt = {'status': 'published_verified', 'sourceKey': data['source_key'], 'provider': 'Naver Cafe',
                   'providerBackend': 'ego', 'category': data['category'], 'providerUrl': url,
                   'providerState': 'public', 'articleId': article_id, 'exactTitle': data['title'],
                   'images': 5, 'quotes': len(data['quotes']), 'ogCards': verified['oglinks'], 'youtubeCards': verified['embeds'],
                   'ctaRaw': True, 'sourceRaw': True, 'sourceLongRaw': True, 'publicVerification': verified,
                   'precommitDuplicateCheck': precheck, 'verifiedAt': stamp, 'doNotRetry': True}
        receipt_path = evidence / '13_provider_evidence.json'
        write(receipt_path, receipt)
        # Retain the pre-submit record under a historical name after success; never erase evidence.
        barrier.replace(evidence / 'ego_submit_barrier_resolved.json')
        write(evidence / 'ego_crm_pending.json', {'sourceKey': data['source_key'], 'providerUrl': url})
        crm = canonical.crm_emit(data['source_key'], receipt_path)
        write(evidence / '14_crm_evidence.json', crm)
        if crm['exitCode'] != 0:
            raise RuntimeError('provider_success_crm_failed')
        receipt['crm'] = {'status': 'success', 'channel': 'naver_cafe', 'stage': 'sent', 'count': 1}
        write(receipt_path, receipt)
        (evidence / 'ego_crm_pending.json').unlink()
        print(json.dumps({'status': 'published_verified', 'providerUrl': url, 'crm': receipt['crm']}))


def recover_pending_tracking(project: Path, queue: dict) -> bool:
    """Recover tracking after verified publication; the stable CRM dedupe key prevents duplicates."""
    import cafe_manifest_publisher as canonical
    with LOCK.open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        for entry in queue.get('entries', []):
            raw = entry.get('provider_evidence')
            if not raw:
                continue
            path = Path(project) / raw
            if not path.is_file():
                continue
            receipt = json.loads(path.read_text())
            if receipt.get('providerBackend') != 'ego' or receipt.get('status') != 'published_verified':
                continue
            if receipt.get('crm', {}).get('status') == 'success':
                continue
            if receipt.get('sourceKey') != entry.get('source_key') or not receipt.get('providerUrl'):
                return False
            verification = receipt.get('publicVerification', {})
            if verification.get('status') != 'verified' or verification.get('oglinks', 0) < 1 or verification.get('embeds', 0) < 1:
                return False
            canonical_url, _ = canonical.canonical_cafe_article_url(receipt['providerUrl'])
            if canonical_url != receipt['providerUrl']:
                return False
            crm = canonical.crm_emit(entry['source_key'], path)
            write(path.with_name('14_crm_evidence.json'), crm)
            if crm['exitCode'] != 0:
                return False
            receipt['crm'] = {'status': 'success', 'channel': 'naver_cafe', 'stage': 'sent', 'count': 1}
            write(path, receipt)
            path.with_name('ego_crm_pending.json').unlink(missing_ok=True)
    return True
