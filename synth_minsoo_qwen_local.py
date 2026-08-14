import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Synthesize Minsoo-style narration with local Qwen3-TTS")
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--reference-audio", type=Path, default=Path("assets/voice_refs/minsoo/minsoo_voice_reference_qwen_55s.wav"))
    parser.add_argument("--reference-text", type=str, default="")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", default="Qwen/Qwen3-TTS-12Hz-0.6B-Base")
    parser.add_argument("--language", default="Korean")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--x-vector-only", action="store_true")
    args = parser.parse_args()

    if not args.script.exists():
        raise SystemExit(f"[error] script not found: {args.script}")
    if not args.reference_audio.exists():
        raise SystemExit(f"[error] reference audio not found: {args.reference_audio}")

    text = args.script.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit("[error] script is empty")

    try:
        import torch
        import soundfile as sf
        from qwen_tts import Qwen3TTSModel
    except ImportError as exc:
        raise SystemExit(
            "[error] qwen-tts runtime is not installed. Install it in a clean Python 3.12 env:\n"
            "        pip install -U qwen-tts soundfile\n"
            f"        missing: {exc}"
        )

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

    if args.x_vector_only or not args.reference_text.strip():
        prompt = model.create_voice_clone_prompt(
            ref_audio=str(args.reference_audio),
            x_vector_only_mode=True,
        )
        wavs, sr = model.generate_voice_clone(
            text=text,
            language=args.language,
            voice_clone_prompt=prompt,
        )
    else:
        wavs, sr = model.generate_voice_clone(
            text=text,
            language=args.language,
            ref_audio=str(args.reference_audio),
            ref_text=args.reference_text,
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(args.out), wavs[0], sr)
    print(f"[done] {args.out}")


if __name__ == "__main__":
    main()
