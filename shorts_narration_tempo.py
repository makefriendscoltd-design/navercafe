"""Retiming of one continuous voice take against the existing reference captions."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def retime(audio, captions, sections, reference, *, parse_srt, duration, clean_token):
    audio, reference = Path(audio), Path(reference)
    ref = parse_srt(reference)
    target = sum(len(x['text'].replace(' ', '')) for x in ref) / (ref[-1]['end'] - ref[0]['start'])
    groups, offset = [], 0
    for section in sections:
        count = sum(bool(clean_token(word)) for word in section.split())
        group = captions[offset:offset + count]
        if len(group) != count or not group:
            raise RuntimeError('Narration logical section does not match subtitle tokens')
        groups.append(group)
        offset += count
    if offset != len(captions):
        raise RuntimeError('Unaccounted subtitle tokens in narration tempo pass')
    end = duration(audio)
    boundaries = [0.0] + [g[0]['start'] for g in groups[1:]] + [end]
    output = audio.parent / 'narration_reference_tempo.mp3'
    if output.exists():
        raise RuntimeError('Tempo candidate already exists; refusing overwrite')
    filters, transformed, records, output_offset = [], [], [], 0.0
    for i, group in enumerate(groups):
        start, stop = boundaries[i:i + 2]
        speech_duration = group[-1]['end'] - group[0]['start']
        count = sum(len(x['text'].replace(' ', '')) for x in group)
        cps = count / speech_duration
        factor = target / cps
        if not .5 <= factor <= 2.0 or stop <= start:
            raise RuntimeError('Required tempo correction exceeds the reviewed range')
        filters.append(f'[0:a]atrim=start={start:.9f}:end={stop:.9f},asetpts=PTS-STARTPTS,atempo={factor:.9f}[s{i}]')
        for item in group:
            transformed.append({**item, 'start': output_offset + (item['start'] - start) / factor,
                                'end': output_offset + (item['end'] - start) / factor})
        records.append({'section': i + 1, 'source_start': start, 'source_end': stop,
                        'output_start': output_offset, 'atempo': factor,
                        'source_characters_per_second': cps,
                        'target_characters_per_second': target})
        output_offset += (stop - start) / factor
    filters.append(''.join(f'[s{i}]' for i in range(len(groups))) + f'concat=n={len(groups)}:v=0:a=1[out]')
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n', '-i', str(audio),
                    '-filter_complex', ';'.join(filters), '-map', '[out]', '-c:a', 'libmp3lame',
                    '-b:a', '192k', str(output)], check=True)
    if [c['text'] for c in transformed] != [c['text'] for c in captions]:
        raise RuntimeError('Tempo pass changed subtitle text')
    report = {'status': 'pass', 'method': 'single_take_pitch_preserving_atempo',
              'reference': str(reference), 'reference_sha256': digest(reference),
              'input_audio_sha256': digest(audio), 'output_audio_sha256': digest(output),
              'target_characters_per_second': target, 'sections': records,
              'text_unchanged': True, 'tts_regenerated': False, 'pitch_shift_applied': False,
              'perceptual_review': 'unverified'}
    (audio.parent / 'narration_tempo_map.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return output, transformed
