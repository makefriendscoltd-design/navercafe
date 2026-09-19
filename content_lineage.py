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
    # Explicit title alternatives are response metadata, not the five body sections.
    text = re.sub(
        r'\A\s*제목 후보\s*1\s*:[^\n]+\n(?:\s*\d+\s*\n)*'
        r'\s*제목 후보\s*2\s*:[^\n]+\n(?:\s*\d+\s*\n)*'
        r'\s*제목 후보\s*3\s*:[^\n]+\n(?:\s*\d+\s*\n)*', '', text,
    )
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



def validate_interrupted_response_adoption(root, source, origin, answer, policy):
    """Validate observed recovery facts without inventing original provider facts."""
    adoption_path = bound_file(root, origin.get('interrupted_response_adoption'), 'interrupted adoption')
    observation_path = bound_file(root, origin.get('recovery_observation'), 'recovery observation')
    ledger_path = bound_file(root, origin.get('attempt_ledger'), 'attempt ledger')
    before = bound_file(root, origin.get('before_submit_screenshot'), 'before screenshot')
    after = bound_file(root, origin.get('after_response_screenshot'), 'after screenshot')
    recovery_shot = bound_file(root, origin.get('recovery_screenshot'), 'recovery screenshot')
    adoption, observation, ledger = map(read_json, (adoption_path, observation_path, ledger_path))
    if (adoption.get('schema') != 'notebooklm-answer-recovery/v1'
            or adoption.get('provenance') != 'adopted_latest_after_interrupted_attempt'
            or adoption.get('source_key') != source or adoption.get('newest_attempt_source') != source
            or adoption.get('notebook_id') != policy.SHORTS_NOTEBOOK['id']
            or adoption.get('notebook_title') != policy.SHORTS_NOTEBOOK['title']
            or adoption.get('account') != policy.ASIDE_ACCOUNT
            or adoption.get('backend') != 'Aside CLI headless REPL'
            or adoption.get('provider_call_made') is not False
            or adoption.get('source_added') is not False or adoption.get('prompt_submitted') is not False
            or Path(adoption.get('recovered_answer_path', '')).resolve() != answer
            or Path(adoption.get('screenshot_path', '')).resolve() != recovery_shot):
        raise LineageError('Interrupted adoption identity/source binding is invalid')
    if (observation.get('schema') != 'notebooklm-interrupted-response-observation/v1'
            or observation.get('source_key') != source
            or observation.get('recovered_answer_sha256') != sha256(answer)
            or observation.get('recovery_pair_index') != adoption.get('pair_index')
            or observation.get('recovery_pair_count') != adoption.get('pair_count')
            or observation.get('recovery_url') != adoption.get('url')
            or observation.get('exactly_one_source_selected_observed') is not True
            or observation.get('completed_response_observed') is not True):
        raise LineageError('Interrupted recovery observation does not bind the adopted answer')
    for entry, path in ((observation.get('historical_before_submit_screenshot') or {}, before),
                        (observation.get('historical_after_response_screenshot') or {}, after),
                        (observation.get('recovery_screenshot') or {}, recovery_shot)):
        if Path(entry.get('path', '')).resolve() != path or entry.get('sha256') != sha256(path):
            raise LineageError('Interrupted recovery screenshot binding is stale')
    matches = [x for x in ledger.get('attempts', []) if x.get('source_key') == source
               and x.get('updated_at') == adoption.get('newest_attempt_at')
               and x.get('attempt_status') == 'unknown_after_provider_start'
               and x.get('instruction_version') == policy.SHORTS_NOTEBOOK_INSTRUCTION_VERSION
               and x.get('instruction_sha256') == policy.SHORTS_NOTEBOOK_INSTRUCTION_SHA256]
    if ledger.get('sourceKey') != source or len(matches) != 1:
        raise LineageError('Interrupted adoption has no exact unknown attempt')
    recovered_at = str(adoption.get('recovered_at') or '')
    candidates = []
    project = ledger_path.parents[3]
    for path in project.glob('outputs/*/shorts/notebooklm-attempt-ledger.json'):
        try: data = read_json(path)
        except (OSError, ValueError): continue
        for attempt in data.get('attempts', []):
            at = str(attempt.get('updated_at') or '')
            if at and at <= recovered_at: candidates.append((at, str(data.get('sourceKey') or '')))
    if not candidates or max(candidates) != (adoption['newest_attempt_at'], source):
        raise LineageError('Source was not the newest attempt at adoption time')
    return {'mode': 'interrupted_response_adoption', 'answer_sha256': sha256(answer),
            'adoption_sha256': sha256(adoption_path), 'observation_sha256': sha256(observation_path)}


def validate_notebook_provider_status(provider: dict) -> str:
    status = provider.get('status')
    if status in {'ok', 'pass', 'ok_recovered_after_cli_timeout'}:
        return str(status)
    if (status == 'response_verified_cleanup_pending'
            and provider.get('restorationRequired') is True
            and provider.get('cleanupRestored') is False
            and provider.get('cleanupReason') in {None, 'notebook_source_state_restore_failed'}):
        return str(status)
    raise LineageError('Shorts NotebookLM provider status is invalid')


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
    script = bound_file(root, origin.get('script'), 'script')
    interrupted = bool(origin.get('interrupted_response_adoption'))
    recovery = {}
    if interrupted:
        recovery = validate_interrupted_response_adoption(root, source, origin, answer, policy)
    else:
        evidence_path = bound_file(root, origin.get('provider_evidence'), 'provider_evidence')
        provider = read_json(evidence_path)
        instruction = provider.get('instructionEvidence') or {}
        if (provider.get('notebookId') != policy.SHORTS_NOTEBOOK['id']
                or instruction.get('sha256') != policy.SHORTS_NOTEBOOK_INSTRUCTION_SHA256
                or instruction.get('version') != policy.SHORTS_NOTEBOOK_INSTRUCTION_VERSION
                or provider.get('targetOnlyBefore') is not True or provider.get('targetOnlyAfter') is not True):
            raise LineageError('Shorts requires current instruction and exact selected-source evidence')
        recovery_entry = origin.get('answer_recovery')
        stored_entry = origin.get('stored_answer')
        if recovery_entry or stored_entry:
            recovery_path = bound_file(root, recovery_entry, 'answer_recovery')
            stored_path = bound_file(root, stored_entry, 'stored_answer')
            if provider.get('answer_sha256') and provider['answer_sha256'] != sha256(stored_path):
                raise LineageError('Provider answer hash differs from saved response')
            recovery = policy.validate_recovered_provider_answer(
                source_key=str(source or ''), stored_answer=stored_path.read_text(encoding='utf-8'),
                recovered_answer=answer.read_text(encoding='utf-8'), evidence=read_json(recovery_path))
        elif provider.get('answer_sha256') and provider['answer_sha256'] != sha256(answer):
            raise LineageError('Provider answer hash differs from saved response')
        validate_notebook_provider_status(provider)
        if (not source or provider.get('account') != policy.ASIDE_ACCOUNT
                or provider.get('notebookTitle') != policy.SHORTS_NOTEBOOK['title']
                or provider.get('sourceUrl', provider.get('source_url')) not in
                {f'https://youtu.be/{source}', f'https://www.youtube.com/watch?v={source}'}):
            raise LineageError('Shorts NotebookLM provider/source binding is invalid')
    raw = answer.read_text(encoding='utf-8').strip()
    shorts.validate_notebooklm_script_layout(shorts._raw_script_body(raw))
    candidates = shorts.extract_head_copy_candidates(raw)
    from notebooklm_source import _strip_citations
    parsed, _ = shorts.extract_script(_strip_citations(raw))
    parsed, _ = shorts.canonicalize_notebooklm_script_layout(parsed)
    transform_path = bound_file(root, origin.get('cta_transform'), 'cta_transform')
    transform = read_json(transform_path)
    keyword = shorts.validate_comment_keyword(str(transform.get('comment_keyword') or ''))
    if (transform.get('status') != 'cta_only'
            or transform.get('cta_style') != 'comment_keyword'):
        raise LineageError('Shorts CTA transform is not bound to a valid comment keyword')
    expected = shorts.finalize_script(parsed, int(origin['source_minutes']), keyword)
    shorts.validate_intro_promise(expected)
    if script.read_text(encoding='utf-8').strip() != expected:
        raise LineageError('Rendered script is not the deterministic NotebookLM + fixed CTA transform')
    wording = origin.get('wording_authorization') or {}
    authorized = (wording.get('scope') == 'preserve_original_absolute_wording'
                  and wording.get('source_key') == source
                  and wording.get('answer_sha256') == sha256(answer)
                  and wording.get('instruction') == '쇼츠는 과장 표현 상관없이 진행한다. 원응답대로 하면된다.')
    fact_entry = origin.get('fact_verifications')
    fact_verifications = []
    if fact_entry:
        fact_verifications = read_json(bound_file(root, fact_entry, 'fact_verifications')).get('verifications')
    claims = policy.validate_shorts_verbatim_claims(
        parsed, preserve_authorized_wording=authorized, fact_verifications=fact_verifications)
    shorts.validate_head_copy_connection(candidates[0], expected)
    report = shorts.cta_only_transform_report(
        parsed, expected, int(origin['source_minutes']), keyword=keyword, provider_answer=raw)
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
    return {'source_key': source, 'script_sha256': sha256(script), 'transform': report,
            'wording_review': claims,
            **({'answer_recovery_review': recovery} if recovery else {})}


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
