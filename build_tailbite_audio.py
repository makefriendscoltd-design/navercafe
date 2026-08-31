"""Build a tightly paced narration master without re-synthesizing the voice.

The input is one ElevenLabs take plus its character alignment.  Long silent
gaps are shortened to a fixed gap, then the Korean subtitle timings are
mapped through the same edit decision list so the burned captions stay in
sync.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    output_start: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=capture,
    )


def duration(path: Path) -> float:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture=True,
    )
    return float(result.stdout.strip())


def detect_silences(path: Path, threshold_db: float, minimum: float) -> list[tuple[float, float]]:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            f"silencedetect=n={threshold_db:g}dB:d={minimum:g}",
            "-f",
            "null",
            "-",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    starts = [float(value) for value in re.findall(r"silence_start: ([0-9.]+)", result.stderr)]
    ends = [float(value) for value in re.findall(r"silence_end: ([0-9.]+)", result.stderr)]
    return list(zip(starts, ends))


def build_segments(
    source_duration: float,
    silences: list[tuple[float, float]],
    retained_gap: float,
) -> list[Segment]:
    half_gap = retained_gap / 2.0
    raw: list[tuple[float, float]] = []
    cursor = 0.0
    for silence_start, silence_end in silences:
        left_end = min(source_duration, max(cursor, silence_start + half_gap))
        if left_end - cursor > 0.001:
            raw.append((cursor, left_end))
        cursor = max(cursor, min(source_duration, silence_end - half_gap))
    if source_duration - cursor > 0.001:
        raw.append((cursor, source_duration))

    segments: list[Segment] = []
    output_cursor = 0.0
    for start, end in raw:
        segments.append(Segment(start=start, end=end, output_start=output_cursor))
        output_cursor += end - start
    return segments


def render_audio(source: Path, output: Path, segments: list[Segment]) -> None:
    if not segments:
        raise RuntimeError("꼬리물기 편집에 사용할 오디오 구간이 없습니다.")
    filters: list[str] = []
    labels: list[str] = []
    for index, segment in enumerate(segments):
        label = f"a{index}"
        filters.append(
            f"[0:a]atrim=start={segment.start:.6f}:end={segment.end:.6f},"
            f"asetpts=PTS-STARTPTS[{label}]"
        )
        labels.append(f"[{label}]")
    filters.append(
        f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1,"
        "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=mono[out]"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(source),
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(output),
        ]
    )


def map_time(value: float, segments: list[Segment]) -> float:
    for segment in segments:
        if segment.start <= value <= segment.end:
            return segment.output_start + value - segment.start
        if value < segment.start:
            return segment.output_start
    last = segments[-1]
    return last.output_start + last.duration


def aligned_words(alignment_path: Path) -> list[tuple[float, float]]:
    payload = json.loads(alignment_path.read_text(encoding="utf-8"))
    alignment = payload["alignment"]
    characters = alignment["characters"]
    starts = alignment["character_start_times_seconds"]
    ends = alignment["character_end_times_seconds"]
    words: list[tuple[float, float]] = []
    word_start: float | None = None
    word_end: float | None = None
    has_text = False
    for character, start, end in zip(characters, starts, ends):
        if str(character).isspace():
            if has_text and word_start is not None and word_end is not None:
                words.append((word_start, word_end))
            word_start = None
            word_end = None
            has_text = False
            continue
        if word_start is None:
            word_start = float(start)
        word_end = float(end)
        has_text = True
    if has_text and word_start is not None and word_end is not None:
        words.append((word_start, word_end))
    return words


def srt_time(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def build_captions(
    script_path: Path,
    alignment_path: Path,
    segments: list[Segment],
    *,
    max_chars: int = 6,
) -> list[dict[str, float | str]]:
    """Create one-full-token captions from the narration alignment.

    ``max_chars`` remains a soft layout target for callers, not a slicing
    instruction.  Splitting a word such as ``Instantly`` (or a long Korean
    eojeol) creates unreadable subtitles and breaks the approved V7 style.
    """
    if max_chars < 1:
        raise ValueError("자막 최대 글자 수는 1 이상이어야 합니다.")
    raw_words = [word for word in re.split(r"\s+", script_path.read_text(encoding="utf-8").strip()) if word]
    timing_words = aligned_words(alignment_path)
    if len(raw_words) != len(timing_words):
        raise RuntimeError(
            f"원고와 정렬 어절 수가 다릅니다: script={len(raw_words)} alignment={len(timing_words)}"
        )

    punctuation = re.compile(r"[,，、.。:;!?！？\"'“”‘’()\[\]{}]")
    captions: list[dict[str, float | str]] = []
    for raw, (start, end) in zip(raw_words, timing_words):
        clean = punctuation.sub("", raw).strip()
        if not clean:
            continue
        mapped_start = map_time(start, segments)
        mapped_end = map_time(end, segments)
        captions.append({"text": clean, "start": mapped_start, "end": mapped_end})
    return captions


def write_srt(path: Path, captions: list[dict[str, float | str]]) -> None:
    rows: list[str] = []
    for index, caption in enumerate(captions, 1):
        start = max(0.0, float(caption["start"]) - 0.02)
        raw_end = float(caption["end"]) + 0.04
        if index < len(captions):
            next_start = max(0.0, float(captions[index]["start"]) - 0.025)
            end = min(raw_end, next_start)
        else:
            end = raw_end
        end = max(start + 0.10, end)
        rows.extend(
            [
                str(index),
                f"{srt_time(start)} --> {srt_time(end)}",
                str(caption["text"]),
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build single-take tail-biting narration and synced SRT.")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--alignment", type=Path, required=True)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--out-audio", type=Path, required=True)
    parser.add_argument("--out-srt", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--threshold-db", type=float, default=-35.0)
    parser.add_argument("--min-silence", type=float, default=0.08)
    parser.add_argument("--retained-gap", type=float, default=0.06)
    args = parser.parse_args()

    source_duration = duration(args.audio)
    silences = detect_silences(args.audio, args.threshold_db, args.min_silence)
    segments = build_segments(source_duration, silences, args.retained_gap)
    render_audio(args.audio, args.out_audio, segments)
    captions = build_captions(args.script, args.alignment, segments)
    write_srt(args.out_srt, captions)

    output_duration = duration(args.out_audio)
    manifest_path = args.manifest or args.out_audio.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(
            {
                "source_audio": str(args.audio),
                "output_audio": str(args.out_audio),
                "srt": str(args.out_srt),
                "source_duration": source_duration,
                "output_duration": output_duration,
                "threshold_db": args.threshold_db,
                "min_silence": args.min_silence,
                "retained_gap": args.retained_gap,
                "silence_count": len(silences),
                "segments": [
                    {
                        "source_start": segment.start,
                        "source_end": segment.end,
                        "output_start": segment.output_start,
                    }
                    for segment in segments
                ],
                "caption_count": len(captions),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"[done] source={source_duration:.3f}s output={output_duration:.3f}s "
        f"silences={len(silences)} captions={len(captions)}"
    )


if __name__ == "__main__":
    main()
