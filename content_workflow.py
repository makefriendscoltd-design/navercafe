"""Channel preparation and read-only audit. Never publishes or silently rewrites."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

from content_lineage import cafe_body_lineage, clean_cafe_answer, read_json, sha256

PROJECT = Path(__file__).resolve().parent


def audit_cafe_queue() -> dict:
    queue = read_json(PROJECT / 'outputs/cafe-publish-queue-20260823/queue.json')
    rows = []
    for entry in queue['entries']:
        row = {'source_key': entry.get('source_key'), 'queue_status': entry.get('status')}
        if entry.get('status') == 'published':
            row['content_validation'] = 'historical_published_not_rechecked'
        else:
            try:
                manifest_path = PROJECT / entry['manifest']
                manifest = read_json(manifest_path)
                answer = manifest.get('notebook_answer') or manifest.get('notebooklm_answer') or (manifest.get('notebooklm') or {}).get('answer') or 'notebooklm/notebooklm-answer.md'
                cafe_body_lineage(manifest_path.parent / answer, manifest_path.parent / manifest['body_file'])
                row['content_validation'] = 'pass'
            except (OSError, ValueError, KeyError) as exc:
                row['content_validation'] = 'blocked'
                row['reason'] = str(exc)
        rows.append(row)
    return {'scope': 'local_files_only', 'provider_requeried': False,
            'queue_counts': dict(Counter(row['queue_status'] for row in rows)), 'entries': rows}


def prepare_cafe(manifest_path: Path, candidate: Path, recovered_answer: Path | None = None) -> dict:
    """Rebuild formatting from the preserved answer, using existing approved images."""
    from notebook_cafe_auto import build_body, count_sections
    manifest_path = manifest_path.resolve()
    manifest = read_json(manifest_path)
    answer = manifest_path.parent / (manifest.get('notebook_answer') or manifest.get('notebooklm_answer')
                                    or (manifest.get('notebooklm') or {}).get('answer')
                                    or 'notebooklm/notebooklm-answer.md')
    original_answer = answer.resolve()
    if recovered_answer is not None:
        answer = recovered_answer.resolve()
    provider = answer.parent / 'notebooklm-provider-evidence.json'
    from cafe_manifest_publisher import validate_notebooklm_cafe_provenance
    origin_manifest = {**manifest, 'notebooklm_answer': str(answer.resolve()),
                       'notebooklm_provider_evidence': str(provider.resolve())}
    origin_manifest.pop('notebook_answer', None)
    # Existing interrupted-response evidence may bind the preserved answer.
    # Never carry that approval over to a replacement answer at another path.
    local_path = manifest_path.parent / '11_local_validation.json'
    existing_local = read_json(local_path) if answer.resolve() == original_answer and local_path.is_file() else {}
    origin = validate_notebooklm_cafe_provenance(manifest_path, origin_manifest, existing_local)
    if not all(origin.values()):
        raise ValueError('NotebookLM answer/provider source binding failed')
    cleaned = clean_cafe_answer(answer.read_text(encoding='utf-8'))
    # Bare NotebookLM heading lines retain their exact words. No generated headings.
    if count_sections(cleaned) != 5:
        lines = cleaned.splitlines()
        headings = [line.strip() for line in lines if 4 <= len(line.strip()) <= 45
                    and not re.search(r'[.!?。]$|^\d+$', line.strip())]
        if len(headings) != 5:
            raise ValueError('Exactly five original NotebookLM headings could not be recovered')
        cleaned = '\n\n'.join('## ' + line.strip() if line.strip() in headings else line.strip()
                              for line in lines if line.strip())
    body = build_body(cleaned, 5, {}, use_ai_keywords=False)
    if body.count('[BLOCKQUOTE]') != 5 or body.count('[IMAGE_HERE]') != 5:
        raise ValueError('Original heading/image structure did not produce five sections')
    candidate.mkdir(parents=True, exist_ok=False)
    body_path = candidate / '03_cafe_body.txt'
    body_path.write_text(body, encoding='utf-8')
    evidence = cafe_body_lineage(answer, body_path)
    manifest['body_file'] = body_path.name
    manifest['notebooklm_answer'] = str(answer.resolve())
    manifest['notebooklm_provider_evidence'] = str(provider.resolve())
    manifest.pop('notebook_answer', None)
    manifest['images'] = [str((manifest_path.parent / image).resolve()) for image in manifest['images']]
    manifest['expected_quote_texts'] = re.findall(r'\[BLOCKQUOTE\](.*?)\[/BLOCKQUOTE\]', body, re.S)
    manifest['content_lineage'] = evidence
    manifest['status'] = 'candidate_requires_editor_and_approval_validation'
    (candidate / '06_cafe_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    (candidate / '11_local_validation.json').write_text(json.dumps({
        'status': 'pass', 'scope': 'content_and_structure_only', 'source_key': manifest['source_key'],
        'checks': {'original_body_preserved': True, 'five_quotes': True, 'five_image_markers': True},
        'asset_status': 'present' if len(manifest['images']) == 5 and all(Path(p).is_file() for p in manifest['images']) else 'missing',
        'evidence': evidence, 'provider_mutation': False,
    }, ensure_ascii=False, indent=2))
    return {'status': 'pass', 'scope': 'local_candidate', 'path': str(candidate), 'provider_mutation': False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    audit = sub.add_parser('audit-cafe')
    audit.add_argument('--out', type=Path)
    cafe = sub.add_parser('prepare-cafe')
    cafe.add_argument('--manifest', required=True, type=Path)
    cafe.add_argument('--candidate', required=True, type=Path)
    cafe.add_argument('--recovered-answer', type=Path)
    cards = sub.add_parser('prepare-cardnews')
    cards.add_argument('--cafe-manifest', required=True, type=Path)
    cards.add_argument('--candidate', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == 'audit-cafe':
        result = audit_cafe_queue()
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            result = {'queue_counts': result['queue_counts'], 'content_counts': dict(Counter(
                row['content_validation'] for row in result['entries'])), 'report': str(args.out)}
    elif args.command == 'prepare-cafe':
        result = prepare_cafe(args.manifest, args.candidate, args.recovered_answer)
    else:
        from youtube_cardnews_pipeline import make_card_deck, make_youtube_post
        manifest_path = args.cafe_manifest.resolve()
        manifest = read_json(manifest_path)
        answer = manifest_path.parent / (manifest.get('notebook_answer') or manifest.get('notebooklm_answer') or 'notebooklm/notebooklm-answer.md')
        provider = manifest_path.parent / (manifest.get('notebooklm_provider_evidence') or 'notebooklm/notebooklm-provider-evidence.json')
        origin = {'mode': 'notebooklm_cafe_summary', 'source_key': manifest['source_key'],
                  'answer': {'path': str(answer), 'sha256': sha256(answer)},
                  'provider_evidence': {'path': str(provider), 'sha256': sha256(provider)}}
        args.candidate.mkdir(parents=True, exist_ok=False)
        try:
            manuscript = clean_cafe_answer(answer.read_text(encoding='utf-8'))
            deck = make_card_deck(manuscript, manifest['title'], content_lineage=origin, evidence_dir=args.candidate)
            (args.candidate / '04_cardnews_deck.json').write_text(json.dumps(deck, ensure_ascii=False, indent=2))
            # The cards are only half of the post; the community body carries the
            # owner's fixed structure and was never written on this path.
            post = make_youtube_post(manuscript)
            (args.candidate / '05_youtube_community_post.txt').write_text(post, encoding='utf-8')
            result = {'status': 'candidate_requires_semantic_and_visual_review', 'path': str(args.candidate),
                      'community_post_chars': len(post), 'provider_mutation': False}
        except Exception as exc:
            (args.candidate / 'generation_failure.json').write_text(json.dumps({'status': 'blocked', 'reason': str(exc), 'content_lineage': origin}, ensure_ascii=False, indent=2))
            raise
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
