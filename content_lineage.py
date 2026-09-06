"""Recompute content provenance from files; never trust self-reported PASS flags."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


class LineageError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def clean_cafe_answer(text: str) -> str:
    """Remove explicit citation/UI syntax, preserving all remaining prose in order."""
    from notebooklm_source import _strip_citations
    text = re.sub(r'\A\s*Thoughts\s*\nexpand_more\s*\n', '', text)
    # Numeric lines are citations only when followed by the UI expansion token
    # or detached sentence punctuation. A standalone factual number is retained.
    text = re.sub(r'\n(?:\d+\s*\n)+(?:more_horiz\s*\n)?(?=[.!?,])', '', text)
    text = _strip_citations(text)
    if re.search(r'(?m)^\s*(?:Thoughts|expand_more|more_horiz)\s*$', text):
        raise LineageError('NotebookLM UI tokens remain; recover the answer without rewriting it')
    return text.strip()


def canonical_prose(text: str) -> str:
    text = re.sub(r'\[(?:/?BOLD|/?HIGHLIGHT|/?BLOCKQUOTE|IMAGE_HERE)\]', '', text)
    text = re.sub(r'^\s{0,3}#{1,6}\s+', '', text, flags=re.M)
    return re.sub(r'\s+', '', text.replace('**', ''))


def cafe_body_lineage(answer: Path, body: Path) -> dict:
    if not answer.is_file() or not body.is_file():
        raise LineageError('NotebookLM answer or actual Cafe body is missing')
    original = canonical_prose(clean_cafe_answer(answer.read_text(encoding='utf-8')))
    actual = canonical_prose(body.read_text(encoding='utf-8'))
    if not original or original != actual:
        raise LineageError('Cafe body differs from NotebookLM answer beyond permitted formatting')
    return {'content_origin': 'notebooklm_cafe', 'notebook_answer_sha256': sha256(answer),
            'body_sha256': sha256(body), 'body_preserved': True}


def bound_file(root: Path, entry: dict, name: str) -> Path:
    if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
        raise LineageError(f'{name}: missing path/hash binding')
    path = (root / entry['path']).resolve()
    if not path.is_file() or sha256(path) != entry.get('sha256'):
        raise LineageError(f'{name}: missing file or stale SHA-256')
    return path


def validate_shorts_origin(root: Path, *, video: Path | None = None) -> dict:
    """Existing production manifest binds the provider answer to rendered speech."""
    import notebooklm_shorts as shorts
    import content_production_policy as policy
    manifest = read_json(root / 'production_manifest.json')
    origin = manifest.get('content_lineage', {})
    if origin.get('mode') != 'notebooklm_verbatim' or manifest.get('content_rewrite_applied'):
        raise LineageError('Shorts requires notebooklm_verbatim; rewritten recovery is not uploadable')
    source = manifest.get('source_id')
    answer = bound_file(root, origin.get('answer'), 'answer')
    evidence_path = bound_file(root, origin.get('provider_evidence'), 'provider_evidence')
    script = bound_file(root, origin.get('script'), 'script')
    provider = read_json(evidence_path)
    instruction = provider.get('instructionEvidence') or {}
    if (provider.get('notebookId') != policy.SHORTS_NOTEBOOK['id']
            or instruction.get('sha256') != policy.SHORTS_NOTEBOOK_INSTRUCTION_SHA256
            or instruction.get('version') != policy.SHORTS_NOTEBOOK_INSTRUCTION_VERSION
            or provider.get('targetOnlyBefore') is not True or provider.get('targetOnlyAfter') is not True):
        raise LineageError('Shorts requires current instruction and exact selected-source evidence')
    if provider.get('answer_sha256') and provider['answer_sha256'] != sha256(answer):
        raise LineageError('Provider answer hash differs from saved response')
    if (not source or provider.get('account') != policy.ASIDE_ACCOUNT
            or provider.get('notebookTitle') != policy.SHORTS_NOTEBOOK['title']
            or provider.get('sourceUrl', provider.get('source_url')) not in
            {f'https://youtu.be/{source}', f'https://www.youtube.com/watch?v={source}'}
            or provider.get('status') not in {'ok', 'pass', 'ok_recovered_after_cli_timeout'}):
        raise LineageError('Shorts NotebookLM provider/source binding is invalid')
    raw = answer.read_text(encoding='utf-8').strip()
    shorts.validate_notebooklm_script_layout(shorts._raw_script_body(raw))
    candidates = shorts.extract_head_copy_candidates(raw)
    from notebooklm_source import _strip_citations
    parsed, _ = shorts.extract_script(_strip_citations(raw))
    parsed, _ = shorts.canonicalize_notebooklm_script_layout(parsed)
    expected = shorts.finalize_script(parsed, int(origin['source_minutes']))
    if script.read_text(encoding='utf-8').strip() != expected:
        raise LineageError('Rendered script is not the deterministic NotebookLM + fixed CTA transform')
    policy.validate_shorts_verbatim_claims(parsed)
    shorts.validate_head_copy_connection(candidates[0], expected)
    report = shorts.cta_only_transform_report(parsed, expected, int(origin['source_minutes']), provider_answer=raw)
    if video is not None:
        if bound_file(root, origin.get('video'), 'video') != video.resolve():
            raise LineageError('Video binding points to another file')
        machine = read_json(root / 'machine_validation.json')
        if machine.get('script_sha256') != sha256(script):
            raise LineageError('Machine validation does not bind the current narration script')
        visual_path = bound_file(root, origin.get('visual_validation'), 'visual_validation')
        visual = read_json(visual_path)
        if visual.get('status') != 'pass' or visual.get('video_sha256') != sha256(video):
            raise LineageError('Visual review does not verify the current video')
        # The provider description must include the exact adopted script.
        upload = read_json(root / '07_provider_manifest.json')
        if (upload.get('source_key') != source or upload.get('final_mp4_sha256') != sha256(video)
                or expected not in upload.get('description', '')):
            raise LineageError('Provider manifest differs from the validated source/video/script')
    return {'source_key': source, 'script_sha256': sha256(script), 'transform': report}


def validate_cardnews_origin(deck: dict) -> dict:
    """Bind generation input and every content card's quoted anchor to the Cafe answer."""
    origin = deck.get('content_lineage', {})
    if origin.get('mode') != 'notebooklm_cafe_summary':
        raise LineageError('Cardnews input must be the Cafe NotebookLM answer')
    answer = bound_file(Path('.'), origin.get('answer'), 'cardnews answer')
    provider_path = bound_file(Path('.'), origin.get('provider_evidence'), 'cardnews provider')
    provider = read_json(provider_path)
    import content_production_policy as policy
    source = origin.get('source_key')
    if (provider.get('account') != policy.ASIDE_ACCOUNT
            or provider.get('notebookTitle') != policy.CAFE_NOTEBOOK['title']
            or provider.get('sourceUrl', provider.get('source_url')) not in
            {f'https://youtu.be/{source}', f'https://www.youtube.com/watch?v={source}'}):
        raise LineageError('Cardnews NotebookLM provider/source binding is invalid')
    original = canonical_prose(clean_cafe_answer(answer.read_text(encoding='utf-8')))
    slides = deck.get('slides', [])
    for slide in slides[1:-1]:
        anchor = canonical_prose(slide.get('source_anchor', ''))
        if len(anchor) < 15 or anchor not in original:
            raise LineageError('Cardnews content slide has no literal NotebookLM evidence anchor')
    return {'content_origin': 'notebooklm_cafe', 'answer_sha256': sha256(answer)}
