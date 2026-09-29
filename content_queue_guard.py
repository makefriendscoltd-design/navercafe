"""Read-only Cafe queue selection and live automation contract check."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo
from cafe_shorts_alignment import aligned_entries, is_shorts_aligned, verified_shorts
from cafe_publication_policy import is_immediate, eligible_entries, publication_block

PROJECT = Path(__file__).resolve().parent
QUEUE = PROJECT / 'outputs/cafe-publish-queue-20260823/queue.json'
PROMPT = QUEUE.with_name('automation_prompt.txt')
AUTOMATION_ID = 'ff795f28-7b3a-49ca-aaa8-65a13c52cde3'


def select_entry(queue: dict, now: datetime, source_key: str | None = None) -> dict | None:
    if is_immediate(queue):
        entries = eligible_entries(PROJECT, queue, now, source_key)
        return entries[0] if entries else None
    if source_key:
        raise ValueError('targeted requests require immediate_on_request mode')
    if is_shorts_aligned(queue):
        entries = aligned_entries(PROJECT, queue, now)
        return entries[0] if entries else None
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
    aligned = is_shorts_aligned(queue)
    due_keys = {e['source_key'] for e in aligned_entries(PROJECT, queue, now)} if aligned else set()
    for entry in queue.get('entries', []):
        if entry.get('status') == 'published' or entry.get('published_url'):
            continue
        if aligned and entry.get('status') in {'pending', 'failed'} and not entry.get('do_not_retry'):
            if entry.get('source_key') in due_keys:
                reason = 'due'
            elif entry.get('status') == 'failed' and entry.get('next_eligible_at') and datetime.fromisoformat(entry['next_eligible_at']) > now:
                reason = 'waiting'
            else:
                reason = 'awaiting_verified_shorts'
        else:
            reason = blocked_reason(entry, now) or 'due'
        counts[reason] = counts.get(reason, 0) + 1
        if reason not in {'due', 'waiting', 'blocked', 'awaiting_verified_shorts'}:
            stuck.append(str(entry.get('source_key')))
    return {'unpublished': sum(counts.values()), 'by_reason': counts,
            'permanently_stuck': sorted(stuck)}


def check_live_prompt() -> None:
    canonical = PROMPT.read_text(encoding='utf-8')
    committed = subprocess.run(['git', 'show', f'HEAD:{PROMPT.relative_to(PROJECT)}'],
                               cwd=PROJECT, text=True, capture_output=True, check=True, timeout=10).stdout
    if canonical != committed:
        raise RuntimeError('canonical_prompt_has_uncommitted_changes')
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--', '*.py', 'cafe_ego_browser.mjs', 'SHORTS_SPEC.md', 'AGENTS.md'],
                   cwd=PROJECT, capture_output=True, check=True, timeout=10)
    proc = subprocess.run(['orca', 'automations', 'show', AUTOMATION_ID, '--json'],
                          text=True, capture_output=True, check=True, timeout=20)
    payload = json.loads(proc.stdout)
    actual = payload['result']['automation']['prompt']
    if actual != canonical:
        raise RuntimeError('live_automation_prompt_differs_from_canonical_file')


def audit(*, live: bool = False, source_key: str | None = None) -> dict:
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
    if live and is_shorts_aligned(queue):
        refresh_shorts_inventory(queue, now)
        now = datetime.now(ZoneInfo('Asia/Seoul'))
    selected = select_entry(queue, now, source_key)
    if is_immediate(queue):
        reason = publication_block(PROJECT, queue, now,
                                   selected.get('source_key') if selected else source_key)
        if reason and reason.startswith('reconcile_required:'):
            return {'status': 'reconcile_required', 'reason': reason, 'provider_mutation': False}
        if selected is None:
            return {'status': 'no_due_entry', 'source_key': source_key,
                    'reason': reason, 'provider_mutation': False,
                    'selection_policy': 'immediate_on_request'}
    if selected is None:
        state = backlog(queue, now)
        # Waiting is normal; a queue where every remaining item is locked is not.
        status = 'no_due_entry' if not state['permanently_stuck'] else 'backlog_locked'
        return {'status': status, 'provider_mutation': False,
                'selection_policy': queue.get('publication_mode', 'legacy'), **state}
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
            'failures': result['failures'], 'provider_mutation': False,
            'shorts_alignment': verified_shorts(PROJECT, selected) if is_shorts_aligned(queue) else None}


def refresh_shorts_inventory(queue: dict, now: datetime) -> None:
    """Refresh expired read-only provider evidence under the shared u0 lock."""
    from cafe_shorts_alignment import inventory_is_fresh
    if inventory_is_fresh(PROJECT, queue, now):
        return
    import fcntl
    from cafe_manifest_publisher import LOCK
    from youtube_shorts_inventory import capture
    with LOCK.open('a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('shorts_inventory_refresh_provider_lock_busy') from exc
        if inventory_is_fresh(PROJECT, queue, now):
            return
        root = PROJECT / 'outputs/cafe-publish-queue-20260823/shorts'
        root.mkdir(parents=True, exist_ok=True)
        result = capture(root)
        if result.get('status') != 'pass' or not inventory_is_fresh(
                PROJECT, queue, datetime.now(ZoneInfo('Asia/Seoul'))):
            raise RuntimeError('shorts_inventory_refresh_failed')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--source-key')
    args = parser.parse_args(argv)
    try:
        result = audit(live=args.live, source_key=args.source_key)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        result = {'status': 'blocked', 'reason': str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
