"""민수 목소리를 로컬에서 만든다. ElevenLabs를 부르지 않는다.

목소리는 이 파이프라인에 남은 유일한 유료 경로였다. 같은 목소리를 이미 로컬 Qwen으로
만들어 쓰는 작업이 있었고(`workspaces/minsoo/패밀리라운지`), 그 설정을 그대로 따른다.
기본값으로 돌리면 발음이 흔들려서 쓸 수 없다 - 아래 값들이 그 차이를 만든다.

- 깨끗한 참조 음성 11.8초와 그 대사. 유튜브 영상에서 잘라낸 음성은 배경음이 섞여 있다.
- float16 + sdpa. float32 는 느리기만 하다.
- 참조 프롬프트를 한 번 만들어 재사용한다.
- top_k 20, top_p 0.85, temperature 0.55. 기본 샘플링은 매번 다르게 발음한다.
- 고정 시드. 같은 대본이면 같은 음성이 나와야 재현할 수 있다.

TTS 는 transformers 4.57 을 요구하고 정렬기는 5.x 를 요구해서 서로 다른 가상환경에
설치돼 있다. 그래서 이 모듈은 제작 파이프라인과 같은 파이썬에서 돌지 않고 별도
파이썬을 불러 쓴다.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
VOICE_PYTHON = PROJECT / ".venv-qwentts/bin/python"
MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
REFERENCE_DIR = Path("/Users/apple/orca/assets/minsoo-voice")
REFERENCE_AUDIO = REFERENCE_DIR / "reference.wav"
REFERENCE_TEXT = REFERENCE_DIR / "reference.txt"
SAMPLING = {"top_k": 20, "top_p": 0.85, "temperature": 0.55,
            "subtalker_top_k": 20, "subtalker_top_p": 0.85, "subtalker_temperature": 0.55}
SEED_BASE = 20260929


class VoiceError(RuntimeError):
    pass


SCRIPT = '''
import json, sys, time, warnings
warnings.filterwarnings("ignore")
import soundfile as sf, torch
from huggingface_hub import snapshot_download
from qwen_tts import Qwen3TTSModel

spec = json.loads(sys.argv[1])
cached = snapshot_download(spec["model"], local_files_only=True)
model = Qwen3TTSModel.from_pretrained(cached, device_map="mps", dtype=torch.float16,
                                      attn_implementation="sdpa", local_files_only=True)
prompt = model.create_voice_clone_prompt(ref_audio=spec["reference_audio"],
                                         ref_text=spec["reference_text"])
torch.manual_seed(spec["seed"])
started = time.monotonic()
wavs, sr = model.generate_voice_clone(text=spec["text"], language="Korean",
                                      voice_clone_prompt=prompt,
                                      max_new_tokens=spec["max_new_tokens"], **spec["sampling"])
sf.write(spec["out"], wavs[0], sr, subtype="PCM_16")
print(json.dumps({"seconds": round(len(wavs[0]) / sr, 3), "sample_rate": sr,
                  "elapsed": round(time.monotonic() - started, 1)}))
'''


def reference_binding() -> dict:
    """참조 음성과 대사를 해시로 묶는다. 목소리가 바뀌면 바로 드러나야 한다."""
    if not REFERENCE_AUDIO.is_file() or not REFERENCE_TEXT.is_file():
        raise VoiceError(f"참조 음성이 없습니다: {REFERENCE_DIR}")
    audio = REFERENCE_AUDIO.read_bytes()
    text = REFERENCE_TEXT.read_text(encoding="utf-8").strip()
    return {"audio": str(REFERENCE_AUDIO), "audio_sha256": hashlib.sha256(audio).hexdigest(),
            "text": text, "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def synthesize(text: str, out_path: Path, *, seed: int | None = None,
               max_new_tokens: int = 2400) -> dict:
    """대본 한 편을 한 번에 읽어 wav 로 저장하고 증거를 돌려준다."""
    if not VOICE_PYTHON.is_file():
        raise VoiceError(f"목소리 전용 파이썬이 없습니다: {VOICE_PYTHON}")
    reference = reference_binding()
    out_path = Path(out_path).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    spec = {"model": MODEL, "reference_audio": reference["audio"],
            "reference_text": reference["text"], "text": text,
            "seed": seed if seed is not None else SEED_BASE,
            "sampling": SAMPLING, "max_new_tokens": max_new_tokens, "out": str(out_path)}
    completed = subprocess.run([str(VOICE_PYTHON), "-c", SCRIPT, json.dumps(spec, ensure_ascii=False)],
                               capture_output=True, text=True, timeout=1800)
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[-300:]
        raise VoiceError(f"로컬 목소리 생성 실패: {tail}")
    line = [x for x in (completed.stdout or "").splitlines() if x.strip().startswith("{")]
    if not line:
        raise VoiceError("생성 결과를 읽지 못했습니다.")
    result = json.loads(line[-1])
    return {"engine": "qwen3-tts-local", "model": MODEL, "device": "mps", "dtype": "float16",
            "attn_implementation": "sdpa", "sampling": SAMPLING, "seed": spec["seed"],
            "reference": reference, "script_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "audio": str(out_path), "audio_sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(),
            "made_at": datetime.now().astimezone().isoformat(timespec="seconds"), **result}


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script_file")
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int)
    args = parser.parse_args(argv)
    text = Path(args.script_file).read_text(encoding="utf-8").strip()
    print(json.dumps(synthesize(text, Path(args.out), seed=args.seed), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
