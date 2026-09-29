#!/usr/bin/env python3
"""Drain eligible Cafe requests sequentially through the existing guarded runner.

Never select a different source, clear a block, or retry the same source during
one drain. The runner retains ownership of eligibility, provider locks and queue
updates. A guard skip, uncertain result or absent progress ends the drain.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from cafe_publication_policy import eligible_entries, is_immediate, publication_block

PROJECT = Path(__file__).resolve().parent
QUEUE_RELATIVE = Path('outputs/cafe-publish-queue-20260823/queue.json')


def read_queue(project: Path) -> dict:
    return json.loads((project / QUEUE_RELATIVE).read_text(encoding='utf-8'))


def records(output: str) -> list[dict]:
    found = []
    for line in output.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            found.append(item)
    return found


def drain(project: Path = PROJECT, *, dry_run: bool = False, timeout: int = 2400) -> dict:
    project = project.resolve()
    attempted: list[dict] = []
    seen: set[str] = set()
    command = [str(project / '.venv312/bin/python'), str(project / 'cafe_queue_runner.py'),
               '--project', str(project)]

    def finish(reason: str, *, status: str = 'stopped') -> dict:
        return {'status': status, 'reason': reason, 'attempted': attempted}

    while True:
        before = read_queue(project)
        if not is_immediate(before):
            return finish('immediate_mode_required')
        now = datetime.now(timezone.utc)
        safety = publication_block(project, before, now, None)
        if safety and safety.startswith('reconcile_required:'):
            return finish(safety)
        eligible = eligible_entries(project, before, now)
        if not eligible:
            return finish('no_eligible_entries', status='complete_for_now')
        # Read the same policy ordering as the runner only to prevent repetition.
        # No source selector is passed: each runner performs its own live guard.
        source = eligible[0]['source_key']
        if source in seen:
            return finish('source_already_attempted')
        blocked = publication_block(project, before, now, source)
        if blocked:
            return finish(blocked)
        if dry_run:
            return {'status': 'dry_run', 'reason': 'no_provider_action',
                    'eligible_source_keys': [e['source_key'] for e in eligible], 'attempted': []}
        try:
            proc = subprocess.run(command, cwd=project, shell=False, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return finish('runner_timeout_provider_state_unverified')
        events = records(proc.stdout or '')
        published_event = next((event for event in events if event.get('action') == 'publish'), None)
        if any(event.get('action') in {'skip', 'blocked', 'abort'} for event in events):
            result = finish('runner_guard_or_safety_stop')
            result['runner_events'] = events
            result['exit_code'] = proc.returncode
            return result
        after = read_queue(project)
        previous = {entry['source_key']: entry for entry in before['entries']}
        current = {entry['source_key']: entry for entry in after['entries']}
        changed = [key for key in current if current[key] != previous.get(key)]
        if not changed:
            return finish('no_queue_progress')
        if (not published_event or len(changed) != 1 or changed[0] != source
                or published_event.get('source_key') != source):
            return finish('unexpected_queue_progress')
        entry = current[source]
        seen.add(source)
        attempted.append({'source_key': source, 'exit_code': proc.returncode,
                          'status': entry.get('status')})
        print(json.dumps({'event': 'entry_completed', **attempted[-1],
                          'provider_url': entry.get('published_url')}, ensure_ascii=False), flush=True)
        if int(entry.get('attempts') or 0) != int(previous[source].get('attempts') or 0) + 1:
            return finish('attempt_progress_unverified')
        uncertain = publication_block(project, after, datetime.now(timezone.utc), source)
        if uncertain and uncertain.startswith('reconcile_required:'):
            return finish(uncertain)
        if (entry.get('status') in {'blocked', 'reconcile_required'}
                or (entry.get('do_not_retry') and entry.get('status') != 'published')):
            return finish('entry_requires_review')
        if entry.get('status') == 'published':
            from urllib.parse import urlparse
            if (not entry.get('published_url')
                    or urlparse(entry['published_url']).hostname != 'cafe.naver.com'):
                return finish('provider_url_unverified')
        elif entry.get('status') == 'failed' and not entry.get('published_url'):
            # Only a recorded pre-publication failure that has entered backoff
            # may yield to another source. Never force a retry eligible now.
            try:
                retry = datetime.fromisoformat(entry['next_eligible_at'])
                if retry.tzinfo is None or retry <= datetime.now(timezone.utc):
                    return finish('failure_backoff_unverified')
            except (KeyError, TypeError, ValueError):
                return finish('failure_backoff_unverified')
        else:
            return finish('provider_state_unverified')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=PROJECT)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--timeout', type=int, default=2400)
    args = parser.parse_args(argv)
    with Path('/tmp/cafe_queue_drain.lock').open('a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            result = {'status': 'stopped', 'reason': 'drain_already_running', 'attempted': []}
        else:
            result = drain(args.project, dry_run=args.dry_run, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in {'complete_for_now', 'dry_run'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
