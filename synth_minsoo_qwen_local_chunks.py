import argparse
import shutil
from pathlib import Path

import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

from synth_minsoo_qwen_space import concat_audio, probe_duration, split_script, write_srt


def main():
    parser = argparse.ArgumentParser(description="Continue Qwen3-TTS chunk synthesis locally")
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--reference-audio", type=Path, default=Path("assets/voice_refs/minsoo/minsoo_voice_reference_qwen_55s.wav"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--srt-out", type=Path)
    parser.add_argument("--chunks-dir", required=True, type=Path)
    parser.add_argument("--max-chars", type=int, default=320)
    parser.add_argument("--model", default="Qwen/Qwen3-TTS-12Hz-0.6B-Base")
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()

    text = args.script.read_text(encoding="utf-8").strip()
    chunks = split_script(text, args.max_chars)
    args.chunks_dir.mkdir(parents=True, exist_ok=True)

    missing = []
    for idx, chunk in enumerate(chunks, start=1):
        chunk_path = args.chunks_dir / f"chunk_{idx:02d}.wav"
        if not chunk_path.exists():
            missing.append((idx, chunk, chunk_path))

    if missing:
        device = "cuda:0" if args.device == "auto" and torch.cuda.is_available() else args.device
        if device == "auto":
            device = "cpu"
        dtype = torch.bfloat16 if "cuda" in device else torch.float32
        model = Qwen3TTSModel.from_pretrained(
            args.model,
            device_map=device,
            dtype=dtype,
            attn_implementation="flash_attention_2" if "cuda" in device else None,
        )
        prompt = model.create_voice_clone_prompt(
            ref_audio=str(args.reference_audio),
            x_vector_only_mode=True,
        )
        for idx, chunk, chunk_path in missing:
            print(f"[info] local synth chunk {idx}/{len(chunks)} chars={len(chunk)}")
            wavs, sr = model.generate_voice_clone(
                text=chunk,
                language="Korean",
                voice_clone_prompt=prompt,
            )
            sf.write(str(chunk_path), wavs[0], sr)
            print(f"[info] wrote {chunk_path}")

    audio_files = []
    timed_chunks = []
    cursor = 0.0
    for idx, chunk in enumerate(chunks, start=1):
        chunk_path = args.chunks_dir / f"chunk_{idx:02d}.wav"
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
