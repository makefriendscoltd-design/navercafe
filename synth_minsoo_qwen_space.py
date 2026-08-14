import argparse
import re
import shutil
import subprocess
from pathlib import Path

from gradio_client import Client, handle_file


def run(cmd: list[str]):
    printable = " ".join(f'"{part}"' if " " in str(part) else str(part) for part in cmd)
    print(f"[run] {printable}")
    subprocess.run(cmd, check=True)


def split_script(text: str, max_chars: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= max_chars:
            current = paragraph
            continue
        sentences = re.split(r"(?<=[.!?。？！요다까죠음니다])\s+", paragraph)
        current = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = sentence
    if current:
        chunks.append(current)
    return chunks


def probe_duration(path: Path) -> float:
    result = subprocess.run(
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
        text=True,
        capture_output=True,
        check=True,
    )
    return float(result.stdout.strip())


def srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: Path, chunks: list[tuple[float, float, str]]):
    lines = []
    idx = 1
    for start, end, text in chunks:
        words = [word for word in re.split(r"\s+", text.replace("\n", " ")) if word]
        if not words:
            continue
        cursor = start
        total = sum(max(1, len(word)) for word in words)
        for word in words:
            dur = max(0.18, (end - start) * max(1, len(word)) / total)
            word_end = min(end, cursor + dur)
            lines.extend([str(idx), f"{srt_time(cursor)} --> {srt_time(word_end)}", word, ""])
            idx += 1
            cursor = word_end
    path.write_text("\n".join(lines), encoding="utf-8")


def concat_audio(files: list[Path], output: Path):
    list_file = output.with_suffix(".txt")
    list_file.write_text(
        "\n".join(f"file '{path.resolve().as_posix()}'" for path in files) + "\n",
        encoding="utf-8",
    )
    run(["ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output)])


def main():
    parser = argparse.ArgumentParser(description="Synthesize Minsoo narration through Qwen3-TTS HF Space")
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--reference-audio", type=Path, default=Path("assets/voice_refs/minsoo/minsoo_voice_reference_qwen_55s.wav"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--srt-out", type=Path)
    parser.add_argument("--chunks-dir", type=Path)
    parser.add_argument("--max-chars", type=int, default=320)
    parser.add_argument("--model-size", choices=["0.6B", "1.7B"], default="0.6B")
    parser.add_argument("--space", default="Qwen/Qwen3-TTS")
    args = parser.parse_args()

    text = args.script.read_text(encoding="utf-8").strip()
    chunks = split_script(text, args.max_chars)
    chunks_dir = args.chunks_dir or args.out.parent / "qwen_chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    print(f"[info] chunks: {len(chunks)}")

    client = Client(args.space)
    audio_files = []
    timed_chunks = []
    cursor = 0.0
    for idx, chunk in enumerate(chunks, start=1):
        chunk_path = chunks_dir / f"chunk_{idx:02d}.wav"
        if not chunk_path.exists():
            print(f"[info] synth chunk {idx}/{len(chunks)} chars={len(chunk)}")
            generated, status = client.predict(
                ref_audio=handle_file(str(args.reference_audio)),
                ref_text="",
                target_text=chunk,
                language="Korean",
                use_xvector_only=True,
                model_size=args.model_size,
                api_name="/generate_voice_clone",
            )
            print(f"[info] {status}")
            shutil.copyfile(generated, chunk_path)
        duration = probe_duration(chunk_path)
        audio_files.append(chunk_path)
        timed_chunks.append((cursor, cursor + duration, chunk))
        cursor += duration

    args.out.parent.mkdir(parents=True, exist_ok=True)
    concat_audio(audio_files, args.out)
    if args.srt_out:
        write_srt(args.srt_out, timed_chunks)
    print(f"[done] {args.out} duration={cursor:.2f}s")


if __name__ == "__main__":
    main()
