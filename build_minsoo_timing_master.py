import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from synth_minsoo_elevenlabs import (
    DEFAULT_VOICE_ID,
    caption_units,
    probe_duration,
    srt_time,
    synthesize_chunk,
)


ROOT = Path(__file__).resolve().parent


def run(cmd: list[str]) -> str:
    printable = " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd)
    print(f"[run] {printable}")
    completed = subprocess.run(cmd, check=True, text=True, capture_output=True)
    if completed.stdout:
        print(completed.stdout.strip())
    if completed.stderr:
        print(completed.stderr.strip())
    return completed.stdout.strip()


def split_script_phrases(text: str, max_chars: int) -> list[str]:
    text = re.sub(r"\r\n?", "\n", text).strip()
    raw_parts: list[str] = []
    for paragraph in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
        pieces = re.split(r"(?<=[.!?\u3002\uFF01\uFF1F]|[요다죠까네음됨임])\s+", paragraph)
        raw_parts.extend(piece.strip() for piece in pieces if piece.strip())

    phrases: list[str] = []
    current = ""
    for part in raw_parts:
        if len(part) <= max_chars:
            candidate = f"{current} {part}".strip() if current else part
            if current and len(candidate) > max_chars:
                phrases.append(current)
                current = part
            else:
                current = candidate
            continue

        clauses = re.split(r"(?<=[,;:\u3001]|고|면|서|데)\s+", part)
        for clause in [c.strip() for c in clauses if c.strip()]:
            candidate = f"{current} {clause}".strip() if current else clause
            if current and len(candidate) > max_chars:
                phrases.append(current)
                current = clause
            else:
                current = candidate
    if current:
        phrases.append(current)
    return phrases


def trim_phrase_audio(input_path: Path, output_path: Path, threshold_db: int):
    audio_filter = (
        f"silenceremove=start_periods=1:start_duration=0.015:start_threshold={threshold_db}dB,"
        "areverse,"
        f"silenceremove=start_periods=1:start_duration=0.035:start_threshold={threshold_db}dB,"
        "areverse,"
        "aresample=44100,aformat=sample_fmts=s16:channel_layouts=mono"
    )
    run([
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(input_path),
        "-af",
        audio_filter,
        str(output_path),
    ])


def concat_with_overlap(parts: list[Path], output_path: Path, overlap: float, target_lufs: float, true_peak: float):
    if not parts:
        raise SystemExit("[error] no phrase audio parts")

    cmd = ["ffmpeg", "-hide_banner", "-y"]
    for part in parts:
        cmd.extend(["-i", str(part)])

    filters: list[str] = []
    if len(parts) == 1:
        filters.append("[0:a]aresample=44100,pan=stereo|c0=c0|c1=c0[aout0]")
        last = "aout0"
    else:
        for index in range(len(parts)):
            filters.append(f"[{index}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=mono[a{index}]")
        filters.append(f"[a0][a1]acrossfade=d={overlap:.3f}:c1=tri:c2=tri[x1]")
        last = "x1"
        for index in range(2, len(parts)):
            out_label = f"x{index}"
            filters.append(f"[{last}][a{index}]acrossfade=d={overlap:.3f}:c1=tri:c2=tri[{out_label}]")
            last = out_label
        filters.append(f"[{last}]pan=stereo|c0=c0|c1=c0[aout0]")

    limiter = 10 ** (true_peak / 20.0)
    filters.append(
        f"[aout0]loudnorm=I={target_lufs:g}:LRA=3:TP={true_peak:g}:linear=true,"
        f"alimiter=limit={limiter:.4f}[aout]"
    )
    cmd.extend([
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[aout]",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(output_path),
    ])
    run(cmd)


def write_timing_srt(phrases: list[str], starts: list[float], ends: list[float], srt_path: Path):
    entries: list[str] = []
    index = 1
    for phrase, start, end in zip(phrases, starts, ends):
        units = caption_units(phrase) or [phrase]
        duration = max(0.05, end - start)
        weights = [max(2, len(re.sub(r"\s+", "", unit))) for unit in units]
        total = sum(weights) or 1
        cursor = start
        for unit_index, (unit, weight) in enumerate(zip(units, weights), start=1):
            unit_end = end if unit_index == len(units) else min(end, cursor + duration * weight / total)
            if unit_end - cursor < 0.05:
                unit_end = min(end, cursor + 0.05)
            if unit_end > cursor:
                entries.append(f"{index}\n{srt_time(cursor)} --> {srt_time(unit_end)}\n{unit}\n")
                index += 1
            cursor = unit_end
    srt_path.write_text("\n".join(entries), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Build the CapCut-style edited narration timing master.")
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="Final overlapped MP3 timing master")
    parser.add_argument("--srt", type=Path, help="Phrase-timed SRT output")
    parser.add_argument("--manifest", type=Path, help="JSON timing manifest output")
    parser.add_argument("--voice-id", default=os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_VOICE_ID))
    parser.add_argument("--api-key", default=os.getenv("ELEVENLABS_API_KEY"))
    parser.add_argument("--model-id", default="eleven_multilingual_v2")
    parser.add_argument("--max-phrase-chars", type=int, default=95)
    parser.add_argument("--overlap", type=float, default=0.10)
    parser.add_argument("--silence-threshold-db", type=int, default=-45)
    parser.add_argument("--target-lufs", type=float, default=-14.0)
    parser.add_argument("--true-peak", type=float, default=-1.8)
    parser.add_argument("--reuse-phrases", action="store_true", help="Reuse existing phrase wav files next to manifest when possible.")
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("[error] ELEVENLABS_API_KEY is required")

    text = args.script.read_text(encoding="utf-8-sig").strip()
    if not text:
        raise SystemExit("[error] script is empty")

    phrases = split_script_phrases(text, args.max_phrase_chars)
    if not phrases:
        raise SystemExit("[error] no phrases produced from script")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    srt_path = args.srt or args.out.with_suffix(".srt")
    manifest_path = args.manifest or args.out.with_suffix(".manifest.json")
    phrase_dir = args.out.with_suffix("")
    phrase_dir.mkdir(parents=True, exist_ok=True)

    print(f"[info] phrases: {len(phrases)}")
    trimmed_parts: list[Path] = []
    phrase_durations: list[float] = []
    for index, phrase in enumerate(phrases, start=1):
        raw = phrase_dir / f"phrase_{index:03d}.mp3"
        trimmed = phrase_dir / f"phrase_{index:03d}_trim.wav"
        if not args.reuse_phrases or not trimmed.exists():
            if not args.reuse_phrases or not raw.exists():
                print(f"[info] synth phrase {index}/{len(phrases)} ({len(phrase)} chars)")
                synthesize_chunk(phrase, raw, args.api_key, args.voice_id, args.model_id)
            trim_phrase_audio(raw, trimmed, args.silence_threshold_db)
        duration = probe_duration(trimmed)
        trimmed_parts.append(trimmed)
        phrase_durations.append(duration)

    concat_with_overlap(trimmed_parts, args.out, args.overlap, args.target_lufs, args.true_peak)

    starts: list[float] = []
    ends: list[float] = []
    cursor = 0.0
    for index, duration in enumerate(phrase_durations):
        start = cursor
        end = start + duration
        starts.append(start)
        ends.append(end)
        cursor = end - args.overlap if index < len(phrase_durations) - 1 else end

    final_duration = probe_duration(args.out)
    if ends:
        scale = final_duration / ends[-1]
        starts = [value * scale for value in starts]
        ends = [value * scale for value in ends]

    write_timing_srt(phrases, starts, ends, srt_path)
    manifest = {
        "script": str(args.script),
        "audio": str(args.out),
        "srt": str(srt_path),
        "voice_id": args.voice_id,
        "overlap": args.overlap,
        "target_lufs": args.target_lufs,
        "true_peak": args.true_peak,
        "duration": final_duration,
        "phrases": [
            {"index": i, "start": s, "end": e, "text": t}
            for i, (s, e, t) in enumerate(zip(starts, ends, phrases), start=1)
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] timing master: {args.out}")
    print(f"[done] srt: {srt_path}")
    print(f"[done] manifest: {manifest_path}")
    print(f"[info] duration: {final_duration:.2f}s")


if __name__ == "__main__":
    main()
