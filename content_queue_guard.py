"""Read-only Cafe queue selection and live automation contract check."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parent
QUEUE = PROJECT / 'outputs/cafe-publish-queue-20260823/queue.json'
PROMPT = QUEUE.with_name('automation_prompt.txt')
AUTOMATION_ID = 'ff795f28-7b3a-49ca-aaa8-65a13c52cde3'


def select_entry(queue: dict, now: datetime) -> dict | None:
    eligible = []
    for entry in queue.get('entries', []):
        if entry.get('published_url') or entry.get('do_not_retry'):
            continue
        status = entry.get('status')
        if status not in {'pending', 'failed'}:
            continue
        if status == 'pending' and entry.get('attempts', 0) != 0:
            continue
        raw = entry.get('not_before') if status == 'pending' else entry.get('next_eligible_at')
        if not raw:
            continue
        when = datetime.fromisoformat(raw)
        if when <= now:
            eligible.append((0 if status == 'pending' else 1, when, entry))
    return min(eligible, key=lambda item: item[:2])[2] if eligible else None


def blocked_reason(entry: dict, now: datetime) -> str | None:
    """Why an unfinished entry cannot be selected, or None when it is due."""
    if entry.get('published_url'):
        return None
    status = entry.get('status')
    # A deliberate block records why; that is a decision, not a stall.
    if status == 'blocked' and entry.get('last_error'):
        return 'blocked'
    if entry.get('do_not_retry'):
        return 'do_not_retry'
    if status not in {'pending', 'failed'}:
        return str(status or 'no_status')
    if status == 'pending' and entry.get('attempts', 0) != 0:
        return 'pending_with_attempts'
    raw = entry.get('not_before') if status == 'pending' else entry.get('next_eligible_at')
    if not raw:
        return 'no_not_before' if status == 'pending' else 'no_next_eligible_at'
    return None if datetime.fromisoformat(raw) <= now else 'waiting'


def backlog(queue: dict, now: datetime) -> dict:
    """Unpublished entries grouped by why they are not selectable.

    ``no_due_entry`` used to read the same whether the queue was healthily
    waiting or every remaining entry was locked out forever. It was the latter
    for days and nothing said so.
    """
    counts: dict[str, int] = {}
    stuck: list[str] = []
    for entry in queue.get('entries', []):
        if entry.get('status') == 'published' or entry.get('published_url'):
            continue
        reason = blocked_reason(entry, now) or 'due'
        counts[reason] = counts.get(reason, 0) + 1
        if reason not in {'due', 'waiting', 'blocked'}:
            stuck.append(str(entry.get('source_key')))
    return {'unpublished': sum(counts.values()), 'by_reason': counts,
            'permanently_stuck': sorted(stuck)}


def check_live_prompt() -> None:
    canonical = PROMPT.read_text(encoding='utf-8')
    committed = subprocess.run(['git', 'show', f'HEAD:{PROMPT.relative_to(PROJECT)}'],
                               cwd=PROJECT, text=True, capture_output=True, check=True, timeout=10).stdout
    if canonical != committed:
        raise RuntimeError('canonical_prompt_has_uncommitted_changes')
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--', '*.py', 'SHORTS_SPEC.md', 'AGENTS.md'],
                   cwd=PROJECT, capture_output=True, check=True, timeout=10)
    proc = subprocess.run(['orca', 'automations', 'show', AUTOMATION_ID, '--json'],
                          text=True, capture_output=True, check=True, timeout=20)
    payload = json.loads(proc.stdout)
    actual = payload['result']['automation']['prompt']
    if actual != canonical:
        raise RuntimeError('live_automation_prompt_differs_from_canonical_file')


def audit(*, live: bool = False) -> dict:
    if live:
        check_live_prompt()
    queue = json.loads(QUEUE.read_text(encoding='utf-8'))
    # A reserved/uncertain publication may already have consumed today's slot.
    for entry in queue.get('entries', []):
        if entry.get('status') == 'reconcile_required':
            return {'status': 'reconcile_required', 'source_key': entry.get('source_key')}
        raw = entry.get('provider_evidence')
        if raw and entry.get('status') != 'published':
            parent = (PROJECT / raw).parent
            if any((parent / name).is_file() for name in (
                'provider_uncertain_do_not_retry.json', 'published_but_verification_failed_do_not_retry.json'
            )):
                return {'status': 'reconcile_required', 'source_key': entry.get('source_key')}
    now = datetime.now(ZoneInfo('Asia/Seoul'))
    selected = select_entry(queue, now)
    if selected is None:
        state = backlog(queue, now)
        # Waiting is normal; a queue where every remaining item is locked is not.
        status = 'no_due_entry' if not state['permanently_stuck'] else 'backlog_locked'
        return {'status': status, 'provider_mutation': False, **state}
    if not selected.get('publisher_command'):
        return {'status': 'blocked', 'source_key': selected.get('source_key'), 'reason': 'legacy_publisher_requires_migration'}
    import cafe_manifest_publisher as publisher
    manifest, _, provider, evidence = publisher.resolve_manifest(selected['manifest'])
    try:
        # A full day or an active success gap is normal scheduler state. Check it
        # before the live YouTube measurement in the eligibility gate so an idle
        # hourly tick neither hits the network nor looks like a broken manifest.
        publisher.enforce_cafe_publish_window(now, source_key=selected.get('source_key'))
    except publisher.CafePublishWindowClosed as exc:
        return {'status': 'throttled', 'source_key': selected.get('source_key'),
                'reason': exc.reason, 'detail': str(exc),
                'provider_mutation': False}
    result = publisher.validate_cafe_eligibility(manifest, provider, evidence)
    return {'status': result['status'], 'source_key': selected.get('source_key'),
            'failures': result['failures'], 'provider_mutation': False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = audit(live=args.live)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        result = {'status': 'blocked', 'reason': str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
