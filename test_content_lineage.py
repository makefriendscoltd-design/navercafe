from pathlib import Path
import json

import pytest

import content_lineage as lineage
import notebooklm_shorts as shorts


def test_cafe_formatting_preserves_original_but_other_body_is_rejected(tmp_path):
    answer, body = tmp_path / 'answer.md', tmp_path / 'body.txt'
    answer.write_text('## 원래 제목\n\n실제 수치는 34개입니다[1].', encoding='utf-8')
    body.write_text('[BLOCKQUOTE]원래 제목[/BLOCKQUOTE]\n실제 수치는 34개입니다.\n[IMAGE_HERE]', encoding='utf-8')
    assert lineage.cafe_body_lineage(answer, body)['body_preserved']
    body.write_text('다른 원고를 작성했습니다.', encoding='utf-8')
    with pytest.raises(lineage.LineageError, match='differs'):
        lineage.cafe_body_lineage(answer, body)


def test_standalone_numbers_are_not_silently_deleted():
    assert '34' in lineage.clean_cafe_answer('실제 수치\n34\n개입니다.')
    assert lineage.clean_cafe_answer('설명입니다\n1\n2\nmore_horiz\n.') == '설명입니다.'


def test_explicit_three_title_candidates_are_metadata_not_body():
    raw = '제목 후보 1: 첫 제목\n1\n 제목 후보 2: 둘째 제목\n2\n 제목 후보 3: 셋째 제목\n3\n4\n첫 소제목\n본문입니다.'
    assert lineage.clean_cafe_answer(raw) == '첫 소제목\n본문입니다.'


def test_a_rewritten_cafe_body_is_rejected(tmp_path):
    """A body that says something the answer does not must never pass."""
    answer = tmp_path / 'notebooklm-answer.md'
    answer.write_text('첫 소제목\n원문 그대로의 문장입니다.', encoding='utf-8')
    body = tmp_path / '03_cafe_body.txt'
    body.write_text('[BLOCKQUOTE]첫 소제목[/BLOCKQUOTE]\n손으로 고쳐 쓴 다른 문장입니다.',
                    encoding='utf-8')
    with pytest.raises(lineage.LineageError, match='differs'):
        lineage.cafe_body_lineage(answer, body)


def test_formatting_markers_alone_do_not_count_as_a_rewrite(tmp_path):
    """The formatter's own markers and headings are not a change of content."""
    answer = tmp_path / 'notebooklm-answer.md'
    answer.write_text('첫 소제목\n원문 그대로의 문장입니다.', encoding='utf-8')
    body = tmp_path / '03_cafe_body.txt'
    body.write_text('[BLOCKQUOTE]첫 소제목[/BLOCKQUOTE]\n\n원문 그대로의 [BOLD]문장[/BOLD]입니다.\n[IMAGE_HERE]',
                    encoding='utf-8')
    assert lineage.cafe_body_lineage(answer, body)['body_preserved'] is True


def test_file_binding_rejects_replaced_artifact(tmp_path):
    path = tmp_path / 'answer.md'
    path.write_text('original')
    entry = {'path': str(path), 'sha256': lineage.sha256(path)}
    assert lineage.bound_file(tmp_path, entry, 'answer') == path
    path.write_text('replacement')
    with pytest.raises(lineage.LineageError, match='stale'):
        lineage.bound_file(tmp_path, entry, 'answer')


def test_real_user_authorization_is_bound_to_the_approved_answer(tmp_path):
    import content_production_policy as policy
    original = Path(__file__).parent / 'outputs/0UFSZ_5OSIk-20260903/repair-20260906/shorts-v17-approved/production_manifest.json'
    if not original.is_file():
        pytest.skip('local approved production response unavailable')
    manifest = json.loads(original.read_text())
    target = tmp_path / 'production_manifest.json'
    target.write_text(json.dumps(manifest))
    # The user's defect report invalidated v17 as a new upload candidate.
    with pytest.raises(lineage.LineageError, match='current instruction'):
        lineage.validate_shorts_origin(tmp_path)


def test_real_factpack_render_is_not_verbatim_upload_authorization():
    root = Path(__file__).parent / 'outputs/dCLW6IQt06M-20260903/shorts/v16-render-20260905'
    if not root.exists():
        pytest.skip('local production artifacts unavailable')
    with pytest.raises(lineage.LineageError, match='rewritten recovery'):
        lineage.validate_shorts_origin(root)


def test_cardnews_factpack_origin_is_rejected():
    with pytest.raises(lineage.LineageError, match='Cafe NotebookLM'):
        lineage.validate_cardnews_origin({'content_lineage': {'mode': 'factpack'}})


def test_cleanup_pending_provider_status_requires_exact_native_cleanup_state():
    provider = {
        'status': 'response_verified_cleanup_pending',
        'restorationRequired': True,
        'cleanupRestored': False,
        'cleanupReason': 'notebook_source_state_restore_failed',
    }
    assert lineage.validate_notebook_provider_status(provider) == provider['status']


def test_cleanup_pending_provider_status_accepts_legacy_persisted_subset():
    provider = {
        'status': 'response_verified_cleanup_pending',
        'restorationRequired': True,
        'cleanupRestored': False,
    }
    assert lineage.validate_notebook_provider_status(provider) == provider['status']


@pytest.mark.parametrize('field,value', [
    ('restorationRequired', False),
    ('cleanupRestored', True),
    ('cleanupReason', 'query_input_missing'),
])
def test_cleanup_pending_provider_status_rejects_unrelated_or_unrestorable_errors(field, value):
    provider = {
        'status': 'response_verified_cleanup_pending',
        'restorationRequired': True,
        'cleanupRestored': False,
        'cleanupReason': 'notebook_source_state_restore_failed',
    }
    provider[field] = value
    with pytest.raises(lineage.LineageError, match='provider status'):
        lineage.validate_notebook_provider_status(provider)


def test_real_six_line_response_is_not_rewritten_by_layout_fix():
    raw = (Path(__file__).parent / 'test_fixtures/notebooklm_shorts/v16_dcl_line_separated.txt').read_text()
    canonical, _ = shorts.canonicalize_notebooklm_script_layout(raw)
    assert canonical.split() == raw.split()


def test_shorts_origin_accepts_exact_transform_and_rejects_rewrite_even_with_fresh_hash(tmp_path):
    import content_production_policy as policy
    raw = ('### 헤드카피라이팅\n'
           '1. 영상 개요가 대박?! / 소스 기반 제작법\n'
           '2. 영상 만들기 어렵죠? / 개요 생성 순서\n'
           '3. 이 기능 놓치면 손해 / 영상 개요 활용법\n\n### 스크립트\n'
           '이 프로그램 대박입니다. 영상 개요의 소스 기반 제작법입니다. 영상 제작 5가지 방법, 저장하고 끝까지 보세요!\n\n'
           '첫째, 사용할 소스를 추가합니다.\n\n둘째, 소스 내용을 확인합니다.\n\n'
           '셋째, 필요한 형식을 고릅니다.\n\n넷째, 영상 개요를 확인합니다.\n\n'
           '다섯째, 결과를 확인합니다.')
    answer, script, evidence = [tmp_path / name for name in ('answer.md', 'script.txt', 'provider.json')]
    answer.write_text(raw)
    parsed, _ = shorts.extract_script(raw)
    headline = shorts.extract_head_copy_candidates(raw)[0]
    keyword = shorts.derive_comment_keyword(parsed, headline)
    script.write_text(shorts.finalize_script(parsed, 12, keyword))
    transform = tmp_path / 'cta-transform.json'
    transform.write_text(json.dumps(shorts.cta_only_transform_report(
        parsed, script.read_text(), 12, keyword=keyword, provider_answer=raw)))
    evidence.write_text(json.dumps({'status': 'ok', 'account': 'u0',
        'notebookTitle': policy.SHORTS_NOTEBOOK['title'], 'notebookId': policy.SHORTS_NOTEBOOK['id'],
        'sourceUrl': 'https://youtu.be/sampleKey01', 'targetOnlyBefore': True, 'targetOnlyAfter': True,
        'instructionEvidence': {'version': policy.SHORTS_NOTEBOOK_INSTRUCTION_VERSION,
                                'sha256': policy.SHORTS_NOTEBOOK_INSTRUCTION_SHA256}}))
    binding = lambda path: {'path': path.name, 'sha256': lineage.sha256(path)}
    manifest = {'source_id': 'sampleKey01', 'content_lineage': {'mode': 'notebooklm_verbatim',
                'answer': binding(answer), 'provider_evidence': binding(evidence),
                'script': binding(script), 'cta_transform': binding(transform), 'source_minutes': 12}}
    path = tmp_path / 'production_manifest.json'
    path.write_text(json.dumps(manifest))
    assert lineage.validate_shorts_origin(tmp_path)['source_key'] == 'sampleKey01'
    script.write_text(script.read_text().replace('소스 내용을 확인합니다', '없는 성과를 추가합니다'))
    manifest['content_lineage']['script'] = binding(script)
    path.write_text(json.dumps(manifest))
    with pytest.raises(lineage.LineageError, match='deterministic'):
        lineage.validate_shorts_origin(tmp_path)


def test_shorts_origin_accepts_explicit_valid_two_letter_keyword_override(tmp_path):
    import content_production_policy as policy
    raw = ('### 헤드카피라이팅\n1. 영상 개요가 대박?! / 소스 기반 제작법\n'
           '2. 영상 만들기 어렵죠? / 개요 생성 순서\n3. 이 기능 놓치면 손해 / 영상 개요 활용법\n\n### 스크립트\n'
           '이 프로그램 대박입니다. 영상 개요의 소스 기반 제작법입니다. 영상 제작 5가지 방법, 저장하고 끝까지 보세요!\n\n'
           '첫째, 사용할 소스를 추가합니다.\n\n둘째, 소스 내용을 확인합니다.\n\n셋째, 필요한 형식을 고릅니다.\n\n'
           '넷째, 영상 개요를 확인합니다.\n\n다섯째, 결과를 확인합니다.')
    answer, script, evidence, transform = [tmp_path / name for name in ('answer.md','script.txt','provider.json','cta-transform.json')]
    answer.write_text(raw); parsed,_=shorts.extract_script(raw)
    script.write_text(shorts.finalize_script(parsed,12,'자료'))
    transform.write_text(json.dumps(shorts.cta_only_transform_report(parsed,script.read_text(),12,keyword='자료',provider_answer=raw)))
    evidence.write_text(json.dumps({'status':'ok','account':'u0','notebookTitle':policy.SHORTS_NOTEBOOK['title'],
      'notebookId':policy.SHORTS_NOTEBOOK['id'],'sourceUrl':'https://youtu.be/sampleKey01','targetOnlyBefore':True,
      'targetOnlyAfter':True,'instructionEvidence':{'version':policy.SHORTS_NOTEBOOK_INSTRUCTION_VERSION,'sha256':policy.SHORTS_NOTEBOOK_INSTRUCTION_SHA256}}))
    bind=lambda p:{'path':p.name,'sha256':lineage.sha256(p)}
    (tmp_path/'production_manifest.json').write_text(json.dumps({'source_id':'sampleKey01','content_lineage':{
      'mode':'notebooklm_verbatim','answer':bind(answer),'provider_evidence':bind(evidence),'script':bind(script),
      'cta_transform':bind(transform),'source_minutes':12}}))
    assert lineage.validate_shorts_origin(tmp_path)['transform']['comment_keyword'] == '자료'


def test_v7_section_split_uses_bound_comment_keyword_and_rejects_old_cta(monkeypatch):
    import shorts_v7_builder as builder
    monkeypatch.setattr(builder, 'SOURCE_MINUTES', 12, raising=False)
    monkeypatch.setattr(builder, 'COMMENT_KEYWORD', '광고')
    body = ('도입입니다.\n\n첫째, 하나.\n\n둘째, 둘.\n\n셋째, 셋.\n\n'
            '넷째, 넷.\n\n다섯째, 다섯.')
    current = body + '\n\n' + shorts.fixed_cta(12, '광고')
    assert builder.split_seven_sections(current)[-1] == shorts.fixed_cta(12, '광고')
    with pytest.raises(RuntimeError, match='source-duration CTA missing'):
        builder.split_seven_sections(body + '\n\n12분 짜리 영상 내용을 모두 정리했습니다.\n\n'
                                     '이 자료 궁금하신 분들은 채널을 구독후 프로필 링크를 확인하세요.')
