"""Prepare one scheduled-source replacement using the canonical production path."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import content_production_policy as policy
import notebooklm_shorts as scripts

PROJECT = Path(__file__).resolve().parent


def binding(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def prepare(source_key, plan_path):
    plan = json.loads(Path(plan_path).read_text())
    entry = next(x for x in plan if x['source_key'] == source_key)
    roots = [p for p in (PROJECT / 'outputs').glob(source_key + '-*') if p.is_dir()]
    if len(roots) != 1:
        raise RuntimeError('Source must have exactly one existing output root')
    source_root = roots[0]
    root = source_root / 'repair-20260906/shorts-v18-continuous-v1'
    if (root / 'final.mp4').exists():
        raise RuntimeError('Candidate already rendered; review/recover it instead of rebuilding')
    subprocess.run([sys.executable, str(PROJECT / 'content_workflow_preflight.py'), '--runtime', '--json'],
                   stdout=(PROJECT / 'outputs/workflow-repair-20260906/latest-batch-preflight.json').open('w'), check=True)
    root.mkdir(parents=True, exist_ok=True)
    source_video = source_root / 'shorts/source_original.mp4'
    old_cfg_path = source_root / 'shorts/render_config.json'
    old_cfg = json.loads(old_cfg_path.read_text()) if old_cfg_path.exists() else {}
    credit = (old_cfg.get('source') or {}).get('text', '')
    # Prefer already verified task-local attribution over a legacy copied renderer.
    for manifest_path in sorted(source_root.glob('repair-*/shorts*/production_manifest.json')):
        old = json.loads(manifest_path.read_text())
        if old.get('source_id') == source_key and old.get('render_inputs', {}).get('source_credit'):
            credit = old['render_inputs']['source_credit']
    metadata_files = sorted((source_root / 'source').glob('*metadata*.json'))
    for metadata_path in metadata_files:
        data = json.loads(metadata_path.read_text())
        if data.get('channel') and (data.get('source_key') == source_key or source_key in str(data.get('url', ''))):
            credit = '출처: ' + data['channel']
    if not source_video.exists():
        import yt_dlp
        source_video = root / 'source_original.mp4'
        if not source_video.exists():
            with yt_dlp.YoutubeDL({'format': '18', 'outtmpl': str(source_video),
                                  'noplaylist': True, 'quiet': True, 'no_warnings': False,
                                  'js_runtimes': {'node': {}},
                                  'extractor_args': {'youtube': {'player_client': ['mweb']}}}) as dl:
                info = dl.extract_info(f'https://youtu.be/{source_key}', download=True)
            if info.get('id') != source_key:
                raise RuntimeError('Downloaded source identity differs')
            credit = '출처: ' + info['channel']
            (root / 'source_download_evidence.json').write_text(json.dumps({
                'source_key': source_key, 'channel': info['channel'], 'title': info['title'],
                'duration_seconds': info['duration'], 'source': binding(source_video),
                'backend': 'project yt-dlp mweb format 18; no cookies',
            }, ensure_ascii=False, indent=2))
    if not credit.startswith('출처: ') or len(credit) <= 4:
        raise RuntimeError('Source attribution is missing')
    script_path = root / '07_script_final.txt'
    if not script_path.exists():
        subprocess.run([sys.executable, str(PROJECT / 'notebooklm_shorts.py'),
                        '--url', f'https://youtu.be/{source_key}', '--out', str(script_path),
                        '--headline-out', str(root / '06_headcopy_candidates.txt'),
                        '--evidence-dir', str(root / 'notebooklm')], check=True)
    script = script_path.read_text().strip()
    scripts.validate_intro_promise(script)
    match = re.search(r'(?m)^(\d+)분 짜리 영상 내용을 모두 정리했습니다\.', script)
    if not match:
        raise RuntimeError('Fixed CTA absent')
    old_script_path = source_root / 'shorts/07_script_final.txt'
    if old_script_path.exists():
        old_cta = re.search(r'(?m)^(\d+)분 짜리 영상 내용을 모두 정리했습니다\.', old_script_path.read_text())
        if old_cta and old_cta[1] != match[1]:
            raise RuntimeError('CTA duration differs from existing scheduled script; inspect source duration')
    import shorts_v7_builder as builder
    builder.SOURCE_MINUTES = int(match[1])
    sections = builder.split_seven_sections(script)
    # A capture mangled by citation chrome is rescued by re-reading the same
    # response; the recovered file is then the verbatim provider answer.
    answer_path = root / 'notebooklm/notebooklm-answer-recovered.md'
    recovery_path = root / 'notebooklm/notebooklm-answer-recovery-evidence.json'
    if answer_path.exists() != recovery_path.exists():
        raise RuntimeError('Recovered NotebookLM answer and its evidence must appear together')
    if not answer_path.exists():
        answer_path = root / 'notebooklm/notebooklm-answer.md'
    heads = scripts.extract_head_copy_candidates(answer_path.read_text())
    title = ' '.join(scripts.head_copy_lines(heads[0]))
    presenter = PROJECT / 'outputs/pw8Bt97U6fk-20260902/repair-20260906/shorts-v18-continuous-v1/production_manifest.json'
    presenter_binding = json.loads(presenter.read_text())['render_inputs']['presenter']
    answer = binding(answer_path)
    manifest = {
        'source_id': source_key, 'content_rewrite_applied': False,
        'provider_mutation_attempted': False, 'studio_opened': False, 'crm_emitted': False,
        'replacement_for_provider_id': entry['provider_id'],
        'content_lineage': {'mode': 'notebooklm_verbatim', 'answer': answer,
            'provider_evidence': binding(root / 'notebooklm/notebooklm-provider-evidence.json'),
            **({'answer_recovery': binding(recovery_path),
                'stored_answer': binding(root / 'notebooklm/notebooklm-answer.md')}
               if recovery_path.exists() else {}),
            'script': binding(script_path), 'source_minutes': int(match[1]),
            'wording_authorization': {'scope': 'preserve_original_absolute_wording',
                'source_key': source_key, 'answer_sha256': answer['sha256'],
                'instruction': '쇼츠는 과장 표현 상관없이 진행한다. 원응답대로 하면된다.'}},
        'render_inputs': {'source': binding(source_video), 'presenter': presenter_binding,
            'headcopy': binding(root / '06_headcopy_candidates.txt'), 'source_credit': credit,
            'upload_title': title,
            'scene_jobs': [section.split('.')[0] for section in sections[:6]],
            'scene_sentinels': [[section.split('.')[0]] for section in sections[1:6]]},
    }
    (root / 'production_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    from content_lineage import validate_shorts_origin
    validate_shorts_origin(root)
    entry.update(repair_stage='original_script_prepared', candidate_root=str(root))
    Path(plan_path).write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    print(json.dumps({'status': 'prepared', 'source_key': source_key, 'root': str(root),
                      'next': 'render, inspect, then replace exact scheduled provider'}, ensure_ascii=False))


def prepare_provider(source_key, plan_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import youtube_shorts_publisher as publisher
    plan = json.loads(Path(plan_path).read_text())
    entry = next(x for x in plan if x['source_key'] == source_key)
    root = Path(entry['candidate_root'])
    target = root / '07_provider_manifest.json'
    if target.exists():
        raise RuntimeError('Provider manifest already exists; resume it without replacing its journal')
    production = json.loads((root / 'production_manifest.json').read_text())
    baseline = json.loads((PROJECT / 'outputs/workflow-repair-20260906/scheduled-shorts-audit/latest-inventory.json').read_text())
    old = next(x for x in baseline['rows'] if x['provider_id'] == entry['provider_id'])
    script = (root / '07_script_final.txt').read_text().strip()
    urls = [f'https://youtu.be/{source_key}', f'https://www.youtube.com/watch?v={source_key}']
    manifest = {'source_key': source_key, 'title': production['render_inputs']['upload_title'],
        'description': script + '\n\n▶ 원본 영상\n' + '\n'.join(urls),
        'final_mp4': str(root / 'final.mp4'), 'final_mp4_sha256': binding(root / 'final.mp4')['sha256'],
        'original_urls': urls, 'expected_channel': '나민수 AI',
        'journal': str(root / 'provider/journal.json'),
        'replacement': {'source_key': source_key, 'provider_id': entry['provider_id'],
            'title': old['title'], 'description_sha256': hashlib.sha256(old['description'].strip().encode()).hexdigest(),
            'scheduled_at': datetime.fromtimestamp(entry['schedule_epoch'], ZoneInfo('Asia/Seoul')).isoformat()}}
    visual = json.loads((root / 'visual_validation.json').read_text())
    if visual.get('status') != 'pass' or visual.get('video_sha256') != manifest['final_mp4_sha256']:
        raise RuntimeError('Actual visual review must pass for the rendered video')
    production['content_lineage']['video'] = binding(root / 'final.mp4')
    production['content_lineage']['visual_validation'] = binding(root / 'visual_validation.json')
    (root / 'production_manifest.json').write_text(json.dumps(production, ensure_ascii=False, indent=2))
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    publisher.validate_local_candidate(publisher.load_manifest(target))
    print(json.dumps({'status': 'provider_manifest_validated', 'manifest': str(target)}, ensure_ascii=False))


def finalize_replacement(source_key, plan_path):
    plan = json.loads(Path(plan_path).read_text())
    entry = next(x for x in plan if x['source_key'] == source_key)
    root = Path(entry['candidate_root'])
    upload = json.loads((root / '07_provider_manifest.json').read_text())
    journal = json.loads(Path(upload['journal']).read_text())
    retired = journal.get('replacement_retirement', {})
    if (journal.get('status') != 'complete' or retired.get('verified_status') != 'private'
            or retired.get('old_provider_id') != entry['provider_id']):
        raise RuntimeError('Exact new schedule and old private state are not both verified')
    verified = journal['verified']
    result_path = root / 'provider/final_result.json'
    if not result_path.exists():
        result_path.write_text(json.dumps({'status': 'complete', 'provider': verified,
            'crm': journal['crm'], 'retirement': retired}, ensure_ascii=False, indent=2))
    production_path = root / 'production_manifest.json'
    production = json.loads(production_path.read_text())
    production.update(provider_mutation_attempted=True, studio_opened=True, crm_emitted=True,
        provider_video_id=verified['provider_id'], provider_url=verified['shorts_url'],
        provider_state='scheduled_private_until_publish', scheduled_at=verified['scheduled_at'],
        provider_reverified=True, provider_evidence=str(result_path),
        supersedes_provider_id=entry['provider_id'])
    production_path.write_text(json.dumps(production, ensure_ascii=False, indent=2))
    old_path = root.parents[1] / 'shorts/production_manifest.json'
    if old_path.exists():
        backup = root / 'provider/predecessor-production-manifest.json'
        if not backup.exists():
            backup.write_bytes(old_path.read_bytes())
        old = json.loads(old_path.read_text())
        if old.get('provider_video_id') != entry['provider_id']:
            raise RuntimeError('Predecessor manifest identity differs; do not overwrite')
        old.update(provider_state='private_replaced', scheduled_at=None,
            canonical_candidate_status='superseded', superseded_by=str(production_path),
            replacement_provider_evidence=str(result_path))
        old_path.write_text(json.dumps(old, ensure_ascii=False, indent=2))
    entry.update(repair_stage='replacement_complete', provider_replacement_verified=True,
        replacement_provider_id=verified['provider_id'], replacement_scheduled_at=verified['scheduled_at'],
        old_provider_status='private', provider_evidence=str(result_path))
    Path(plan_path).write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    print(json.dumps({'status': 'replacement_complete', 'source_key': source_key,
        'complete': sum(x.get('provider_replacement_verified') is True for x in plan),
        'total': len(plan)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_key')
    parser.add_argument('--plan', default=str(PROJECT / 'outputs/workflow-repair-20260906/scheduled-shorts-audit/sequential-replacement-plan.json'))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--provider-only', action='store_true', help='Bind a rendered and visually reviewed candidate to its original schedule')
    mode.add_argument('--finalize', action='store_true', help='Update existing runtime manifests only after both provider states are verified')
    args = parser.parse_args()
    (finalize_replacement if args.finalize else prepare_provider if args.provider_only else prepare)(args.source_key, args.plan)
