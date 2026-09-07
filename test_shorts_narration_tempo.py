import array
import math
import shutil
import subprocess
import wave

import pytest

from content_production_policy import SHORTS_NARRATION_TARGET_CPS
from shorts_narration_tempo import retime


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='ffmpeg required')
def test_real_audio_tempo_changes_duration_without_changing_pitch_or_words(tmp_path):
    audio = tmp_path / 'voice.wav'
    rate = 16000
    samples = array.array('h', (int(12000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(int(3.5 * rate))))
    with wave.open(str(audio), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.tobytes())
    reference = tmp_path / 'reference.srt'
    reference.write_text('reference fixture')
    # Two characters per 0.4 s cue is 5 cps, slower than the target, so the pass
    # speeds the take up the way it does for the real voice.
    sections = ['하나', '둘셋', '넷다', '섯여', '일곱', '여덟', '아홉']
    captions = [{'text': text, 'start': i * .5, 'end': i * .5 + .4} for i, text in enumerate(sections)]
    output, transformed = retime(audio, captions, sections, reference,
        parse_srt=lambda path: [{'text': '하나둘셋넷다', 'start': 0, 'end': .5}],
        duration=lambda path: 3.5, clean_token=lambda word: word)
    assert [x['text'] for x in transformed] == sections
    for item in transformed:
        assert (len(item['text']) / (item['end'] - item['start'])
                == pytest.approx(SHORTS_NARRATION_TARGET_CPS))
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(output), '-f', 's16le', '-ac', '1', '-ar', str(rate), '-'])
    decoded = array.array('h')
    decoded.frombytes(raw)
    actual_duration = len(decoded) / rate
    assert actual_duration < 3.5
    crossings = sum(a <= 0 < b for a, b in zip(decoded, decoded[1:]))
    assert crossings / actual_duration == pytest.approx(440, abs=6)
