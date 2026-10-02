"""Accept a Cafe publication request and attempt its guarded queue entry once."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

PROJECT = Path(__file__).resolve().parent
QUEUE_RELATIVE = Path('outputs/cafe-publish-queue-20260823/queue.json')


def publish_enrolled(source_key: str, *, project: Path = PROJECT, timeout: int = 2100) -> dict:
    """Use the runner CLI so its process lock and live guard remain mandatory."""
    project = project.resolve()
    queue_path = project / QUEUE_RELATIVE
    queue = json.loads(queue_path.read_text(encoding='utf-8'))
    entry = next((e for e in queue['entries'] if e['source_key'] == source_key), None)
    if entry is None:
        return {'status': 'not_enrolled', 'source_key': source_key, 'published': False}
    from cafe_publication_policy import is_immediate
    if not is_immediate(queue):
        return {'status': 'queued', 'source_key': source_key, 'published': False}
    command = [str(project / '.venv312/bin/python'), str(project / 'cafe_queue_runner.py'),
               '--project', str(project), f'--source-key={source_key}']
    try:
        proc = subprocess.run(command, cwd=project, shell=False, capture_output=True,
                              text=True, timeout=timeout)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        # Do not change retry state: the provider child may still be finishing.
        exit_code = None
    current = json.loads(queue_path.read_text(encoding='utf-8'))
    entry = next(e for e in current['entries'] if e['source_key'] == source_key)
    from cafe_manifest_publisher import canonical_cafe_article_url
    url = entry.get('published_url')
    try:
        if urlparse(url or '').hostname != 'cafe.naver.com':
            raise ValueError('not a Naver Cafe provider URL')
        canonical, _ = canonical_cafe_article_url(url or '')
    except (ValueError, TypeError, RuntimeError):
        canonical = None
    published = entry.get('status') == 'published' and bool(canonical)
    return {'status': 'published' if published else 'deferred', 'source_key': source_key,
            'published': published, 'queue_status': entry.get('status'),
            'runner_exit_code': exit_code, 'provider_url': canonical if published else None}


def request(manifest: Path | None = None, source_key: str | None = None) -> dict:
    if manifest is not None:
        data = json.loads(manifest.read_text(encoding='utf-8'))
        if source_key and source_key != data.get('source_key'):
            raise ValueError('request source_key differs from manifest')
        source_key = data['source_key']
        from cafe_queue_enroll import enroll
        result = enroll(manifest)
        if result['status'] not in {'enrolled', 'refreshed', 'already_enrolled'}:
            return {**result, 'published': False}
    if not source_key:
        raise ValueError('manifest or source_key is required')
    return publish_enrolled(source_key)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--source-key')
    args = parser.parse_args(argv)
    if not args.manifest and not args.source_key:
        parser.error('--manifest or --source-key is required')
    result = request(args.manifest, args.source_key)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in {'published', 'queued'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
