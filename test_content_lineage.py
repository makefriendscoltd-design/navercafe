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


def test_real_cafe_rewrite_is_rejected():
    root = Path(__file__).parent / 'outputs/0UFSZ_5OSIk-20260903/cafe'
    if not root.exists():
        pytest.skip('local production artifacts unavailable')
    with pytest.raises(lineage.LineageError, match='differs'):
        lineage.cafe_body_lineage(root / 'notebooklm/notebooklm-answer.md', root / '03_cafe_body.txt')


def test_file_binding_rejects_replaced_artifact(tmp_path):
    path = tmp_path / 'answer.md'
    path.write_text('original')
    entry = {'path': str(path), 'sha256': lineage.sha256(path)}
    assert lineage.bound_file(tmp_path, entry, 'answer') == path
    path.write_text('replacement')
    with pytest.raises(lineage.LineageError, match='stale'):
        lineage.bound_file(tmp_path, entry, 'answer')


def test_real_factpack_render_is_not_verbatim_upload_authorization():
    root = Path(__file__).parent / 'outputs/dCLW6IQt06M-20260903/shorts/v16-render-20260905'
    if not root.exists():
        pytest.skip('local production artifacts unavailable')
    with pytest.raises(lineage.LineageError, match='rewritten recovery'):
        lineage.validate_shorts_origin(root)


def test_cardnews_factpack_origin_is_rejected():
    with pytest.raises(lineage.LineageError, match='Cafe NotebookLM'):
        lineage.validate_cardnews_origin({'content_lineage': {'mode': 'factpack'}})


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
           '이 프로그램 대박입니다. 영상 개요의 소스 기반 제작법입니다.\n\n'
           '첫째, 사용할 소스를 추가합니다.\n\n둘째, 소스 내용을 확인합니다.\n\n'
           '셋째, 필요한 형식을 고릅니다.\n\n넷째, 영상 개요를 확인합니다.\n\n'
           '다섯째, 결과를 확인합니다.')
    answer, script, evidence = [tmp_path / name for name in ('answer.md', 'script.txt', 'provider.json')]
    answer.write_text(raw)
    parsed, _ = shorts.extract_script(raw)
    script.write_text(shorts.finalize_script(parsed, 12))
    evidence.write_text(json.dumps({'status': 'ok', 'account': 'u0',
        'notebookTitle': policy.SHORTS_NOTEBOOK['title'], 'notebookId': policy.SHORTS_NOTEBOOK['id'],
        'sourceUrl': 'https://youtu.be/sampleKey01', 'targetOnlyBefore': True, 'targetOnlyAfter': True,
        'instructionEvidence': {'version': policy.SHORTS_NOTEBOOK_INSTRUCTION_VERSION,
                                'sha256': policy.SHORTS_NOTEBOOK_INSTRUCTION_SHA256}}))
    binding = lambda path: {'path': path.name, 'sha256': lineage.sha256(path)}
    manifest = {'source_id': 'sampleKey01', 'content_lineage': {'mode': 'notebooklm_verbatim',
                'answer': binding(answer), 'provider_evidence': binding(evidence),
                'script': binding(script), 'source_minutes': 12}}
    path = tmp_path / 'production_manifest.json'
    path.write_text(json.dumps(manifest))
    assert lineage.validate_shorts_origin(tmp_path)['source_key'] == 'sampleKey01'
    script.write_text(script.read_text().replace('소스 내용을 확인합니다', '없는 성과를 추가합니다'))
    manifest['content_lineage']['script'] = binding(script)
    path.write_text(json.dumps(manifest))
    with pytest.raises(lineage.LineageError, match='deterministic'):
        lineage.validate_shorts_origin(tmp_path)
