import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent
DEFAULT_VOICE_ID = "34bevfaPHev7LXnjGAlA"


def run(cmd: list[str]) -> str:
    completed = subprocess.run(cmd, check=True, text=True, capture_output=True)
    return completed.stdout.strip()


def probe_duration(path: Path) -> float:
    out = run([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nw=1:nk=1",
        str(path),
    ])
    return float(out)


def srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt_time(value: str) -> float:
    hh, mm, rest = value.split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def split_for_tts(text: str, max_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            sentences = re.split(r"(?<=[.!?。！？요다까죠음니다])\s+", paragraph)
        else:
            sentences = [paragraph]
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            candidate = f"{current}\n\n{sentence}".strip() if current else sentence
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = sentence
    if current:
        chunks.append(current)
    return chunks


def split_caption_phrase(phrase: str, max_chars: int = 10, max_words: int = 2) -> list[str]:
    phrase = re.sub(r"\s+", " ", phrase).strip()
    if not phrase:
        return []
    words = phrase.split()
    chunks: list[str] = []
    current: list[str] = []

    def current_len(items: list[str]) -> int:
        return len(re.sub(r"\s+", "", " ".join(items)))

    for word in words:
        candidate = current + [word]
        if current and (current_len(candidate) > max_chars or len(candidate) > max_words):
            chunks.append(" ".join(current))
            current = [word]
        else:
            current = candidate
    if current:
        chunks.append(" ".join(current))
    return chunks


def clean_caption_text(text: str) -> str:
    text = re.sub(r"[,，、.。:;!?！？]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def caption_units(text: str) -> list[str]:
    units: list[str] = []
    for paragraph in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
        sentences = re.split(r"(?<=[.!?。！？요다까죠음니다])\s+", paragraph)
        for sentence in sentences:
            sentence = re.sub(r"\s+", " ", sentence).strip()
            if not sentence:
                continue
            marked = re.sub(r"([,，、:;])\s*", r"\1|", sentence)
            marked = re.sub(r"(하고|되고|열고|누르고|넣고|고치고|확인하고|띄우고|표시하고)\s+", r"\1|", marked)
            marked = re.sub(r"(하면|이라면|채)\s+", r"\1|", marked)
            marked = re.sub(r"(습니다\.?|합니다\.?|됩니다\.?|겁니다\.?|입니다\.?|죠\.?|요\.?|다\.?)\s+", r"\1|", marked)
            phrases = marked.split("|")
            for phrase in phrases:
                phrase = clean_caption_text(phrase)
                if not phrase:
                    continue
                units.extend(clean_caption_text(unit) for unit in split_caption_phrase(phrase) if clean_caption_text(unit))
    return units


def word_caption_units(text: str) -> list[str]:
    cleaned = clean_caption_text(text)
    if not cleaned:
        return []
    return [word for word in cleaned.split() if word]


def synthesize_chunk(text: str, output_path: Path, api_key: str, voice_id: str, model_id: str):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_192"
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.44,
            "similarity_boost": 0.82,
            "style": 0.22,
            "use_speaker_boost": True,
        },
    }
    response = requests.post(
        url,
        headers={
            "xi-api-key": api_key,
            "accept": "audio/mpeg",
            "content-type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    if response.status_code >= 400:
        raise SystemExit(f"[error] ElevenLabs {response.status_code}: {response.text[:500]}")
    output_path.write_bytes(response.content)


def concat_audio(parts: list[Path], output_path: Path):
    if len(parts) == 1:
        output_path.write_bytes(parts[0].read_bytes())
        return
    list_path = output_path.with_suffix(".concat.txt")
    lines = [f"file '{part.as_posix()}'" for part in parts]
    list_path.write_text("\n".join(lines), encoding="utf-8")
    run(["ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(output_path)])
    list_path.unlink(missing_ok=True)


def write_weighted_srt(text: str, audio_path: Path, srt_path: Path):
    duration = probe_duration(audio_path)
    units = caption_units(text)
    weights = [max(6, len(re.sub(r"\s+", "", unit))) for unit in units]
    total = sum(weights) or 1
    cursor = 0.0
    entries = []
    for index, (unit, weight) in enumerate(zip(units, weights), start=1):
        seg_dur = max(0.8, duration * weight / total)
        end = duration if index == len(units) else min(duration, cursor + seg_dur)
        entries.append(f"{index}\n{srt_time(cursor)} --> {srt_time(end)}\n{unit}\n")
        cursor = end
    srt_path.write_text("\n".join(entries), encoding="utf-8")


def read_srt_events(path: Path) -> list[tuple[float, float, str]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    events: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start_raw, end_raw = [part.strip().split()[0] for part in lines[1].split("-->")]
        caption = " ".join(lines[2:])
        events.append((parse_srt_time(start_raw), parse_srt_time(end_raw), caption))
    return events


def write_retimed_meaning_srt(source_srt: Path, output_srt: Path, word_units: bool = False):
    entries = []
    index = 1
    for start, end, caption in read_srt_events(source_srt):
        units = word_caption_units(caption) if word_units else caption_units(caption)
        if not units:
            continue
        duration = max(0.01, end - start)
        weights = [max(2, len(re.sub(r"\s+", "", unit))) for unit in units]
        total = sum(weights) or 1
        cursor = start
        for unit_index, (unit, weight) in enumerate(zip(units, weights), start=1):
            unit_end = end if unit_index == len(units) else min(end, cursor + duration * weight / total)
            entries.append(f"{index}\n{srt_time(cursor)} --> {srt_time(unit_end)}\n{unit}\n")
            cursor = unit_end
            index += 1
    output_srt.write_text("\n".join(entries), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Generate AIMAX narration with ElevenLabs.")
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--srt", type=Path)
    parser.add_argument("--voice-id", default=os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_VOICE_ID))
    parser.add_argument("--api-key", default=os.getenv("ELEVENLABS_API_KEY"))
    parser.add_argument("--model-id", default="eleven_multilingual_v2")
    parser.add_argument("--max-chars", type=int, default=1300)
    parser.add_argument("--srt-only", action="store_true", help="Reuse --out audio and only regenerate SRT.")
    parser.add_argument("--retime-from-srt", type=Path, help="Split an existing Whisper SRT into short meaning-unit captions.")
    parser.add_argument("--word-units", action="store_true", help="Use whitespace word captions inside each Whisper segment.")
    args = parser.parse_args()

    if not args.srt_only and not args.retime_from_srt and not args.api_key:
        raise SystemExit("[error] ELEVENLABS_API_KEY is required")

    text = args.script.read_text(encoding="utf-8-sig").strip()
    if not text:
        raise SystemExit("[error] script is empty")

    if args.retime_from_srt:
        srt_path = args.srt or args.out.with_suffix(".srt")
        write_retimed_meaning_srt(args.retime_from_srt, srt_path, word_units=args.word_units)
        print(f"[done] retimed srt: {srt_path}")
        return

    if not args.srt_only:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        chunks = split_for_tts(text, args.max_chars)
        print(f"[info] ElevenLabs chunks: {len(chunks)}")

        with tempfile.TemporaryDirectory(prefix="elevenlabs_") as temp_dir:
            temp = Path(temp_dir)
            parts = []
            for index, chunk in enumerate(chunks, start=1):
                part = temp / f"chunk_{index:02d}.mp3"
                print(f"[info] synth chunk {index}/{len(chunks)} ({len(chunk)} chars)")
                synthesize_chunk(chunk, part, args.api_key, args.voice_id, args.model_id)
                parts.append(part)
            concat_audio(parts, args.out)

    srt_path = args.srt or args.out.with_suffix(".srt")
    write_weighted_srt(text, args.out, srt_path)
    print(f"[done] audio: {args.out}")
    print(f"[done] srt: {srt_path}")
    print(f"[info] duration: {probe_duration(args.out):.2f}s")


if __name__ == "__main__":
    main()
