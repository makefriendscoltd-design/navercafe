import hashlib
import json

import pytest

import notebooklm_shorts as scripts
import shorts_v7_builder as builder


def test_recent_long_intro_is_rejected_before_narration():
    from pathlib import Path
    path = Path('outputs/0UFSZ_5OSIk-20260903/repair-20260906/shorts-v17-approved/07_script_final.txt')
    if not path.exists():
        pytest.skip('production artifact unavailable')
    with pytest.raises(RuntimeError, match='5가지 방법'):
        scripts.validate_intro_promise(path.read_text())


def test_short_intro_with_five_method_promise_is_accepted():
    scripts.validate_intro_promise('이 프로그램 대박입니다. 클로드로 반복 업무를 맡깁니다. 업무 자동화 5가지 방법, 저장하고 끝까지 보세요!\n\n첫째, 폴더를 연결합니다.')


def test_complete_script_is_generated_once_and_sections_only_label_timing(monkeypatch, tmp_path):
    sections = ['도입 문장', '첫째 행동', '둘째 행동', '셋째 행동', '넷째 행동', '다섯째 행동', '고정 CTA']
    script = '\n\n'.join(sections)
    script_path = tmp_path / 'script.txt'
    script_path.write_text(script)
    monkeypatch.setattr(builder, 'ROOT', tmp_path)
    monkeypatch.setattr(builder, 'SCRIPT', script_path)
    calls = []
    def synthesize(text, audio, alignment, **kwargs):
        calls.append((text, kwargs))
        audio.write_bytes(b'one-provider-audio-response')
        alignment.write_text(json.dumps({'script_sha256': hashlib.sha256(text.encode()).hexdigest(), 'original_alignment_text_matches_script': True}))
    monkeypatch.setattr(builder, 'generate_minsoo_section', synthesize)
    monkeypatch.setattr(builder.tailbite, 'aligned_words', lambda path: [(i, i + .5) for i in range(14)])
    audio, alignment, records = builder.generate_single_take(sections)
    assert len(calls) == 1
    assert calls[0][0] == script
    assert calls[0][1]['previous_text'] is None
    assert calls[0][1]['next_text'] is None
    assert len(records) == 7
    assert {r['audio'] for r in records} == {str(audio)}
    assert all(r['logical_section_only'] for r in records)
    assert json.loads(alignment.read_text())['generation_request_count'] == 1
    with pytest.raises(RuntimeError, match='refusing overwrite'):
        builder.generate_single_take(sections)
    assert len(calls) == 1


def test_female_speaker_original_keeps_the_same_strong_hook_contract():
    scripts.validate_intro_promise('이 여자 미쳤습니다. 자신의 생산성과 일상 데이터를 인공지능에게 학습시켜 집중력을 높였습니다. 나만의 AI 집중 시스템 만드는 5가지 방법, 저장하고 끝까지 보세요!\n\n첫째, 기록합니다.')
