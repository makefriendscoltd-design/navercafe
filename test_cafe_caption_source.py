import json
from pathlib import Path

import pytest

import cafe_caption_source as captions
from content_lineage import sha256, validate_cardnews_origin


def test_writer_and_cli_prepare_reuse_without_notebooklm(tmp_path, monkeypatch):
    import subscription_agent
    from notebook_cafe_auto import build_body
    from content_lineage import cafe_body_lineage
    root = tmp_path / 'abcdefghijk-20260929'
    src = root / 'shorts/captions'
    src.mkdir(parents=True)
    transcript = src / 'transcript.txt'
    transcript.write_text('원본 영상의 전사문입니다.\n' * 30)
    evidence = {'source_key': 'abcdefghijk', 'url': 'https://youtu.be/abcdefghijk',
                'transcript': str(transcript), 'transcript_sha256': sha256(transcript),
                'segment_count': 30}
    (src / 'evidence.json').write_text(json.dumps(evidence))
    paragraph = '영상에서는 작업을 시작하기 전에 자료를 확인하는 과정을 설명합니다. 필요한 자료를 모은 다음 내용을 살펴보고 작업 순서를 정합니다. 이 순서에 따라 확인합니다. '
    manuscript = '# 반복 업무를 정리하는 방법\n\n' + paragraph + '\n\n' + '\n\n'.join(f'## 작업 순서 {i}\n\n{paragraph}\n\n{paragraph}' for i in range(5))
    calls = []
    def generate(prompt, **kwargs):
        assert captions.policy.CAFE_NOTEBOOK_PROMPT in prompt
        assert transcript.read_text() in prompt
        calls.append(prompt)
        return manuscript
    monkeypatch.setattr(subscription_agent, 'run', generate)
    out = captions.prepare('abcdefghijk', root)
    assert len(calls) == 1
    assert captions.prepare('abcdefghijk', root) == out
    assert len(calls) == 1
    import subprocess, sys
    run = subprocess.run([sys.executable, str(Path(captions.__file__)),
                          '--root', str(root), '--', 'abcdefghijk'],
                         capture_output=True, text=True, check=True)
    assert json.loads(run.stdout)['status'] == 'pass'
    manifest_path = root / 'cafe/06_cafe_manifest.json'
    manifest = {'source_key': 'abcdefghijk', 'manuscript_source': 'captions',
                'manuscript_answer': out['answer'], 'manuscript_evidence': str(root / 'cafe/writer/evidence.json')}
    assert all(captions.validate_provenance(manifest_path, manifest).values())
    body = root / 'cafe/body.txt'
    body.write_text(build_body(manuscript, 5, {}, use_ai_keywords=False))
    assert cafe_body_lineage(Path(out['answer']), body, content_origin='captions_cafe')['body_preserved']
    origin = {'mode': 'captions_cafe_summary', 'source_key': 'abcdefghijk',
              'answer': {'path': out['answer'], 'sha256': out['answer_sha256']},
              'provider_evidence': {'path': manifest['manuscript_evidence'], 'sha256': sha256(Path(manifest['manuscript_evidence']))}}
    assert validate_cardnews_origin({'content_lineage': origin, 'slides': []})['content_origin'] == 'captions_cafe'
    transcript.write_text('changed source')
    assert not captions.validate_provenance(manifest_path, manifest)['provider_evidence_exact']
    with pytest.raises(ValueError, match='invalid'):
        captions.prepare('abcdefghijk', root)


def test_wrong_source_never_generates(tmp_path, monkeypatch):
    import subscription_agent
    root = tmp_path / 'abcdefghijk'
    src = root / 'shorts/captions'
    src.mkdir(parents=True)
    (src / 'evidence.json').write_text(json.dumps({'source_key': 'other-source', 'segment_count': 30}))
    monkeypatch.setattr(subscription_agent, 'run', lambda *a, **k: pytest.fail('must not generate'))
    with pytest.raises(ValueError, match='mismatch'):
        captions.prepare('abcdefghijk', root)


def test_legacy_instruction_hash_remains_exact():
    assert captions.prompt_hash('cafe-caption/v1') == '008bbae507e8645f6e27bfa92b7ee70ab4db4ba10b9d8d4717adc0e57ab3c3ab'
    legacy = {'instruction_sha256': captions.prompt_hash('cafe-caption/v1')}
    assert captions.evidence_version(legacy) == 'cafe-caption/v1'
    with pytest.raises(ValueError, match='mismatched'):
        captions.evidence_version({**legacy, 'instruction_version': captions.policy.CAFE_INSTRUCTION_VERSION})
    with pytest.raises(ValueError, match='mismatched'):
        captions.evidence_version({'instruction_sha256': captions.prompt_hash()})
    with pytest.raises(ValueError, match='mismatched'):
        captions.evidence_version({'instruction_sha256': 'forged', 'instruction_version': 'unknown'})


def _business_column(sections=7):
    paragraph = '반복 작업을 맡길 때 필요한 자료와 확인할 결과물을 먼저 정합니다. 영상에서 설명한 입력 순서를 따라 작은 작업을 시작하고 결과를 살펴보는 방법입니다. '
    return '# 반복 업무를 맡기는 순서\n\n' + paragraph + '\n\n' + '\n\n'.join(
        f'## 업무 단계 {i}\n\n{paragraph}\n\n{paragraph}' for i in range(sections))


def test_business_column_accepts_intro_variable_sections_and_prose_comparison():
    for count in (4, 7, 8):
        answer = _business_column(count)
        if count == 4:
            answer += '\n\n' + '비교할 때는 입력 자료와 결과 확인 순서를 구분합니다. ' * 10
        captions.validate_answer(answer)
    captions.validate_answer(_business_column() + '\n\n1. 자료를 먼저 준비합니다.\n2. 결과물을 확인합니다.')


@pytest.mark.parametrize('alter,match', [
    (lambda s: s.replace('# 반복 업무를 맡기는 순서\n\n', ''), 'title'),
    (lambda s: s.replace('## 업무 단계 6', '### 업무 단계 6'), 'headings'),
    (lambda s: s + '\n\n| 항목 | 방법 |\n| --- | --- |\n| 입력 | 자료 |', 'pipe tables'),
    (lambda s: s + '\n\nhttps://example.com', 'CTA'),
    (lambda s: s + '\n\n## 빈 구간\n', 'prose'),
    (lambda s: s + '가' * 5000, 'characters'),
])
def test_business_column_rejects_unsupported_or_incomplete_format(alter, match):
    with pytest.raises(ValueError, match=match):
        captions.validate_answer(alter(_business_column()))


def test_legacy_body_format_still_validates_under_legacy_version():
    paragraph = '영상에서는 작업을 시작하기 전에 자료를 확인하는 과정을 설명합니다. 필요한 자료를 모은 다음 내용을 살펴보고 작업 순서를 정합니다. 이 순서에 따라 확인합니다. '
    old = '\n\n'.join(f'## 작업 순서 {i}\n\n{paragraph}\n\n{paragraph}' for i in range(5))
    captions.validate_answer(old, 'cafe-caption/v1')
    with pytest.raises(ValueError, match='title'):
        captions.validate_answer(old)
