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
    manuscript = '\n\n'.join(f'## 작업 순서 {i}\n\n{paragraph}\n\n{paragraph}' for i in range(5))
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
