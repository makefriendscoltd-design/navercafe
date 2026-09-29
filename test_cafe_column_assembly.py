"""Formatting regressions for preserved column prose and approved source assets."""
import json

import pytest

from content_lineage import cafe_body_lineage
from notebook_cafe_auto import build_body, count_sections


def column(section_count):
    intro = '# 사업자가 정할 업무 기준\n\n도구를 도입하기 전에 반복 업무와 확인할 결과부터 정해보세요.'
    prose = ('영상에서 소개한 기능을 실제 업무에 연결하려면 입력 자료와 결과를 확인해야 합니다. '
             '상품 설명을 만드는 상황을 예로 들면 자료를 정리한 다음 상품 정보와 비교할 수 있습니다. '
             '이것은 제품의 성능 보장이 아닌 업무 적용 예시입니다.')
    return intro + '\n\n' + '\n\n'.join(
        f'## 업무 기준 {i}\n\n{prose}\n\n확인한 결과와 남은 작업을 기록해 다음 판단에 사용하세요.'
        for i in range(section_count))


@pytest.mark.parametrize('section_count', [4, 6, 7, 8])
def test_column_keeps_title_intro_headings_and_exactly_five_images(tmp_path, section_count):
    manuscript = column(section_count)
    body = build_body(manuscript, 5, {'column_layout': True}, use_ai_keywords=False)
    assert count_sections(manuscript) == section_count
    assert body.startswith('사업자가 정할 업무 기준\n\n도구를 도입하기 전에')
    assert body.count('[BLOCKQUOTE]') == section_count
    assert body.count('[IMAGE_HERE]') == 5
    assert body.index('[IMAGE_HERE]') > body.index('[BLOCKQUOTE]')
    answer = tmp_path / 'answer.md'
    formatted = tmp_path / 'body.txt'
    answer.write_text(manuscript)
    formatted.write_text(body)
    assert cafe_body_lineage(answer, formatted, content_origin='captions_cafe')['body_preserved']


def test_legacy_five_sections_keep_one_image_per_section(tmp_path):
    manuscript = '\n\n'.join(f'## 원본 제목 {i}\n\n첫 문단입니다.\n\n둘째 문단입니다.' for i in range(5))
    body = build_body(manuscript, 5, {}, use_ai_keywords=False)
    expected = '\n\n'.join(f'[BLOCKQUOTE]원본 제목 {i}[/BLOCKQUOTE]\n\n첫 문단입니다.\n\n둘째 문단입니다.\n\n[IMAGE_HERE]' for i in range(5))
    assert body == expected
    assert count_sections(manuscript) == 5


@pytest.mark.parametrize('section_count', [6, 7])
def test_prepare_column_candidate_uses_existing_images_and_preserves_answer(tmp_path, monkeypatch, section_count):
    import cafe_caption_source as captions
    from content_workflow import prepare_cafe
    cafe = tmp_path / 'cafe'
    cafe.mkdir()
    answer = cafe / 'answer.md'
    answer.write_text(column(section_count))
    receipt = cafe / 'evidence.json'
    receipt.write_text(json.dumps({'instruction_version': 'cafe-business-column/v2',
                                  'instruction_sha256': captions.prompt_hash('cafe-business-column/v2')}))
    images = []
    for index in range(5):
        image = cafe / f'approved-{index}.jpg'
        image.write_bytes(b'preserved-existing-asset')
        images.append(image.name)
    original = {'source_key': 'abcdefghijk', 'manuscript_source': 'captions',
                'manuscript_answer': answer.name, 'manuscript_evidence': receipt.name,
                'title': 'Old title', 'images': images, 'body_file': 'old.txt'}
    manifest_path = cafe / '06_cafe_manifest.json'
    manifest_path.write_text(json.dumps(original))
    monkeypatch.setattr(captions, 'validate_provenance', lambda *args: {'bound': True})
    candidate = tmp_path / 'candidate'
    assert prepare_cafe(manifest_path, candidate)['status'] == 'pass'
    result = json.loads((candidate / '06_cafe_manifest.json').read_text())
    assert result['title'] == '사업자가 정할 업무 기준'
    assert result['expected_quotes'] == section_count
    assert result['expected_images'] == 5
    assert result['images'] == [str((cafe / name).resolve()) for name in images]
    assert cafe_body_lineage(answer, candidate / '03_cafe_body.txt')['body_preserved']
    assert json.loads(manifest_path.read_text()) == original
    assert 'five_quotes' not in json.loads((candidate / '11_local_validation.json').read_text())['checks']
