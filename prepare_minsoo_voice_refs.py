import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent


@dataclass
class Segment:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def run(cmd: list[str], *, capture: bool = False):
    printable = " ".join(f'"{part}"' if " " in str(part) else str(part) for part in cmd)
    print(f"[run] {printable}")
    return subprocess.run(cmd, check=True, text=True, capture_output=capture)


def probe_duration(path: Path) -> float:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(path),
        ],
        capture=True,
    )
    return float(result.stdout.strip())


def detect_silences(path: Path, noise_db: float, min_duration: float) -> list[tuple[float, float | None]]:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            f"silencedetect=n={noise_db}dB:d={min_duration}",
            "-f",
            "null",
            "-",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", result.stderr)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", result.stderr)]
    return [(start, ends[idx] if idx < len(ends) else None) for idx, start in enumerate(starts)]


def speech_segments(duration: float, silences: list[tuple[float, float | None]], padding: float) -> list[Segment]:
    segments = []
    cursor = 0.0
    for silence_start, silence_end in silences:
        silence_end = duration if silence_end is None else silence_end
        end = max(cursor, silence_start + padding)
        if end - cursor > 0.5:
            segments.append(Segment(cursor, min(duration, end)))
        cursor = max(cursor, silence_end - padding)
    if duration - cursor > 0.5:
        segments.append(Segment(cursor, duration))
    return segments


def choose_reference_chunks(segments: list[Segment], target_duration: float, max_chunks: int) -> list[Segment]:
    candidates = [segment for segment in segments if 4.0 <= segment.duration <= 18.0]
    candidates.sort(key=lambda segment: (-segment.duration, segment.start))
    selected = []
    total = 0.0
    for segment in candidates:
        selected.append(segment)
        total += segment.duration
        if len(selected) >= max_chunks or total >= target_duration:
            break
    return sorted(selected, key=lambda segment: segment.start)


def extract_wav(source: Path, output: Path, start: float | None = None, duration: float | None = None):
    cmd = ["ffmpeg", "-hide_banner", "-y"]
    if start is not None:
        cmd.extend(["-ss", f"{start:.3f}"])
    cmd.extend(["-i", str(source)])
    if duration is not None:
        cmd.extend(["-t", f"{duration:.3f}"])
    cmd.extend(
        [
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-af",
            "highpass=f=80,lowpass=f=9000,loudnorm=I=-18:TP=-2:LRA=11",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )
    run(cmd)


def concat_wavs(files: list[Path], output: Path):
    list_file = output.with_suffix(".txt")
    list_file.write_text(
        "\n".join(f"file '{path.resolve().as_posix()}'" for path in files) + "\n",
        encoding="utf-8",
    )
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(output),
        ]
    )


def main():
    parser = argparse.ArgumentParser(description="Prepare Minsoo voice reference WAVs from local recordings")
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "assets" / "voice_refs" / "minsoo")
    parser.add_argument("--noise-db", type=float, default=-38.0)
    parser.add_argument("--min-silence", type=float, default=0.45)
    parser.add_argument("--target-duration", type=float, default=55.0)
    parser.add_argument("--max-chunks-per-file", type=int, default=4)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_chunks = []
    summary = ["source,start,end,duration,wav"]
    for source in args.inputs:
        source = source.resolve()
        if not source.exists():
            raise SystemExit(f"[error] input not found: {source}")
        duration = probe_duration(source)
        silences = detect_silences(source, args.noise_db, args.min_silence)
        speech = speech_segments(duration, silences, padding=0.08)
        chunks = choose_reference_chunks(speech, args.target_duration / len(args.inputs), args.max_chunks_per_file)
        print(f"[info] {source.name}: duration={duration:.2f}s speech_segments={len(speech)} chunks={len(chunks)}")
        for idx, segment in enumerate(chunks, start=1):
            chunk_path = args.out_dir / f"{source.stem}_ref_{idx:02d}.wav"
            extract_wav(source, chunk_path, segment.start, segment.duration)
            all_chunks.append(chunk_path)
            summary.append(f"{source.name},{segment.start:.3f},{segment.end:.3f},{segment.duration:.3f},{chunk_path.name}")

    if all_chunks:
        concat_wavs(all_chunks, args.out_dir / "minsoo_voice_reference_pack.wav")
    (args.out_dir / "manifest.csv").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(f"[done] {args.out_dir}")


if __name__ == "__main__":
    main()
