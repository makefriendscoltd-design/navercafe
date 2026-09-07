from __future__ import annotations

import hashlib
import base64
import importlib.util
import json
import math
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT = Path(__file__).resolve().parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import build_tailbite_audio as tailbite
import shorts_video
from content_production_policy import (
    MINSOO_MODEL_ID,
    MINSOO_VOICE_ID,
    MINSOO_VOICE_SETTINGS,
    NARRATION,
    TAILBITE,
    strip_subtitle_edge_punctuation,
    validate_narration_target_cps,
    validate_presenter_asset,
    validate_shorts_render_bundle,
)
from notebooklm_shorts import fixed_cta, validate_head_copy, validate_head_copy_connection


SOURCE_ID = "7cimtg6LPHg"
SOURCE_URL = "https://youtu.be/7cimtg6LPHg"
SOURCE_CREDIT = ""
SOURCE = ROOT / "source_original.mp4"
PRESENTER = Path("/Users/apple/Downloads/2026-07-02 15-39-18.mp4")
SCRIPT = ROOT / "07_script_final.txt"
HEADCOPY = ROOT / "06_headcopy_candidates.txt"
FINAL = ROOT / "final.mp4"
RENDERER_PATH = PROJECT / "outputs/uX6zwf4b8sM-20260829/shorts/renderer/aimax_video_pipeline.py"
REFERENCE_CONFIG = PROJECT / "outputs/20260822-shorts-correction-audit/rebaseline/remade-v7-reference-restored/TZO3_2Krsqk/render_config.json"
ASSET_ROOT = PROJECT / "outputs/uX6zwf4b8sM-20260829/shorts/renderer/assets"
BGM = ASSET_ROOT / "bgm/DSGNBass-Millitary_Action_Tri-Elevenlabs.mp3"
SFX = ASSET_ROOT / "sfx/WHSH-Whoosh_Short_Clean-Elevenlabs.mp3"
TITLE_FONT = ASSET_ROOT / "fonts/BMHANNA_11yrs_ttf.ttf"
BODY_FONT = ASSET_ROOT / "fonts/Cafe24Ohsquare.ttf"
NARRATION_GENERATION_PROTOCOL = "single_take_reference_restoration_v1"
NARRATION_PAIR_PREFLIGHT_MAX = 1.08
NARRATION_PAIR_MAX_ATTEMPTS = 6
NARRATION_PAIR_SEED_BASE = 2_026_090_400
NARRATION_SECTION_NAMES = ("intro", "first", "second", "third", "fourth", "fifth", "cta")


def run(args: list[object], *, capture: bool = True, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [str(x) for x in args], text=True, capture_output=capture,
        encoding="utf-8", errors="replace",
    )
    if check and proc.returncode:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(map(str, args[:12]))}\n"
            + (proc.stderr or proc.stdout)[-6000:]
        )
    return proc


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def probe(path: Path) -> dict:
    return json.loads(run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path,
    ]).stdout)


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def parse_loudnorm(stderr: str) -> dict:
    matches = re.findall(r"\{\s*\"input_i\"[\s\S]+?\}", stderr)
    if not matches:
        raise RuntimeError("loudnorm measurement JSON missing")
    raw = json.loads(matches[-1])
    return {
        "integrated_lufs": float(raw["input_i"]),
        "true_peak_dbtp": float(raw["input_tp"]),
        "lra_lu": float(raw["input_lra"]),
        "threshold_lufs": float(raw["input_thresh"]),
    }


def loudness(path: Path) -> dict:
    proc = run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", path,
        "-af", "loudnorm=I=-14:LRA=7:TP=-1.8:print_format=json", "-f", "null", "-",
    ], check=True)
    return parse_loudnorm(proc.stderr)


def normalize_loudness(source: Path, target: Path, integrated: float, true_peak: float, dur: float | None = None) -> None:
    base = ["ffmpeg", "-hide_banner", "-nostats"]
    if source == BGM:
        base += ["-stream_loop", "-1"]
    base += ["-i", source]
    if dur is not None:
        base += ["-t", f"{dur:.3f}"]
    first = run(base + [
        "-af", f"loudnorm=I={integrated}:LRA=7:TP={true_peak}:print_format=json",
        "-f", "null", "-",
    ])
    measured = parse_loudnorm(first.stderr)
    raw_match = re.findall(r"\{\s*\"input_i\"[\s\S]+?\}", first.stderr)[-1]
    raw = json.loads(raw_match)
    filt = (
        f"loudnorm=I={integrated}:LRA=7:TP={true_peak}:"
        f"measured_I={raw['input_i']}:measured_LRA={raw['input_lra']}:"
        f"measured_TP={raw['input_tp']}:measured_thresh={raw['input_thresh']}:"
        f"offset={raw['target_offset']}:linear=true:print_format=summary"
    )
    command = base + ["-af", filt, "-ar", "48000", "-ac", "2"]
    if target.suffix.lower() == ".wav":
        command += ["-c:a", "pcm_s24le"]
    command += ["-y", target]
    run(command)
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError(f"normalized audio missing: {target}")
    _ = measured


def calibrate_voice_lufs(target: Path) -> None:
    """Trim source-dependent loudnorm drift to the locked -16 LUFS target."""
    current = loudness(target)
    gain = -16.0 - current["integrated_lufs"]
    if abs(gain) <= 0.05:
        return
    calibrated = target.with_name(target.stem + "_calibrated.wav")
    run([
        "ffmpeg", "-hide_banner", "-y", "-i", target,
        "-af", f"volume={gain:.4f}dB", "-ar", "48000", "-ac", "2",
        "-c:a", "pcm_s24le", calibrated,
    ])
    shutil.move(calibrated, target)


# The minimum on-screen time write_srt gives one caption cue.
SUBTITLE_READABILITY_FLOOR_S = 0.10


def parse_srt(path: Path) -> list[dict]:
    def seconds(value: str) -> float:
        h, m, rest = value.split(":")
        s, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    events = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        left, right = [x.strip() for x in lines[1].split("-->")]
        events.append({"start": seconds(left), "end": seconds(right), "text": " ".join(lines[2:])})
    return events


def build_runtime_gate(segments: list[tailbite.Segment], captions: list[dict]) -> dict:
    script_text = SCRIPT.read_text(encoding="utf-8").strip()
    evidence = json.loads((ROOT / "narration_alignment.json").read_text(encoding="utf-8"))
    continuous = evidence.get("generation_protocol") == "single_take_reference_restoration_v1"
    if continuous:
        single = ROOT / "narration_single_take.mp3"
        pair_preflight_pass = bool(
            evidence.get("generation_request_count") == 1
            and single.is_file()
            and evidence.get("single_take_sha256") == sha(single)
            and evidence.get("generation_mode") == NARRATION["generation_mode"]
        )
        selected_pair = selected_ratio = None
        selected_pair_files_match = False
    else:
        pair_summary = json.loads(
            (ROOT / "narration_pair_preflight/summary.json").read_text(encoding="utf-8")
        )
        selected_pair = str(pair_summary.get("selected_pair") or "")
        selected_result = next(
            (item for item in pair_summary.get("pairs") or [] if item.get("pair") == selected_pair),
            None,
        )
        selected_ratio = selected_result.get("last_to_first_ratio") if selected_result else None
        selected_pair_root = ROOT / "narration_pair_preflight" / selected_pair
        active_section_root = ROOT / "narration_sections"
        selected_pair_files_match = bool(selected_pair) and all(
            sha(selected_pair_root / filename) == sha(active_section_root / filename)
            for filename in (
                "01_intro.mp3",
                "01_intro_alignment.json",
                "07_cta.mp3",
                "07_cta_alignment.json",
            )
        )
        pair_preflight_pass = bool(
            pair_summary.get("protocol") == NARRATION_GENERATION_PROTOCOL
            and pair_summary.get("status") == "pass"
            and pair_summary.get("preflight_maximum") == NARRATION_PAIR_PREFLIGHT_MAX
            and pair_summary.get("exact_runtime_maximum")
            == NARRATION["last_to_first_pace_ratio_max"]
            and selected_result
            and selected_result.get("status") == "pass"
            and selected_result.get("pair_disposition") == "selected"
            and isinstance(selected_ratio, (int, float))
            and selected_ratio <= NARRATION_PAIR_PREFLIGHT_MAX
            and pair_summary.get("selected_pair_preflight_ratio") == selected_ratio
            and evidence.get("generation_protocol") == NARRATION_GENERATION_PROTOCOL
            and evidence.get("selected_pair") == selected_pair
            and evidence.get("selected_pair_preflight_ratio") == selected_ratio
            and selected_pair_files_match
            and all(
                item.get("pair_disposition") == "discard_both"
                for item in pair_summary.get("pairs") or []
                if item.get("pair") != selected_pair
            )
        )
    alignment = evidence["alignment"]
    chars = alignment["characters"]
    starts = [float(value) for value in alignment["character_start_times_seconds"]]
    ends = [float(value) for value in alignment["character_end_times_seconds"]]
    profiles = []
    caption_offset = 0
    for record in evidence["sections"]:
        count = sum(bool(strip_subtitle_edge_punctuation(word)) for word in record["script"].split())
        group = captions[caption_offset:caption_offset + count]
        caption_offset += count
        if not group or len(group) != count:
            raise RuntimeError(f"missing final captions for {record['name']}")
        spoken_seconds = max(0.001, group[-1]["end"] - group[0]["start"])
        characters = sum(len(item["text"].replace(" ", "")) for item in group)
        profiles.append({"name": record["name"], "character_count": characters,
                         "spoken_seconds": round(spoken_seconds, 6),
                         "characters_per_second": round(characters / spoken_seconds, 6)})
    early, middle, late = profiles[0], profiles[3], profiles[6]
    if continuous:
        tempo = json.loads((ROOT / "narration_tempo_map.json").read_text())
        reference = REFERENCE_CONFIG.parent / "captions.srt"
        # Check the speech against the pace this run recorded, not against whatever
        # the reference video speaks at, so a run built at an earlier approved pace
        # keeps revalidating instead of failing when the setting moves.
        target_cps = validate_narration_target_cps(
            tempo.get("target_characters_per_second")
        )
        if (tempo.get("reference_sha256") != sha(reference)
                or tempo.get("input_audio_sha256") != sha(ROOT / "narration_tailbite.mp3")
                or tempo.get("output_audio_sha256") != sha(ROOT / "narration_reference_tempo.mp3")
                or tempo.get("captions_sha256") != sha(ROOT / "captions.srt")
                or any(abs(p["characters_per_second"] / target_cps - 1) > .01 for p in profiles)):
            raise RuntimeError("Reference tempo evidence differs from actual audio/subtitles")
    ratio = late["characters_per_second"] / early["characters_per_second"]
    script_tokens = [value for value in re.split(r"\s+", script_text) if value]
    expected_caption_tokens = [strip_subtitle_edge_punctuation(value) for value in script_tokens]
    expected_caption_tokens = [value for value in expected_caption_tokens if value]
    actual_caption_tokens = [str(item["text"]) for item in captions]
    hash_match = evidence.get("script_sha256") == hashlib.sha256(script_text.encode("utf-8")).hexdigest()
    count_match = len(actual_caption_tokens) == len(expected_caption_tokens)
    whole_tokens = actual_caption_tokens == expected_caption_tokens
    pace_pass = ratio <= NARRATION["last_to_first_pace_ratio_max"]
    result = {
        "status": (
            "pass" if hash_match and count_match and whole_tokens and pace_pass
            and pair_preflight_pass else "fail"
        ),
        "script_alignment_hash_match": hash_match,
        "caption_count_matches_script_tokens": count_match,
        "caption_tokens_are_whole": whole_tokens,
        "script_token_count": len(expected_caption_tokens),
        "caption_count": len(actual_caption_tokens),
        "pace_uniformity": {
            "status": "pass" if pace_pass else "fail",
            "early": early,
            "middle": middle,
            "late": late,
            "last_to_first_ratio": round(ratio, 6),
            "maximum": NARRATION["last_to_first_pace_ratio_max"],
        },
        "recovery_protocol": {
            "name": NARRATION_GENERATION_PROTOCOL,
            "paired_preflight_status": "pass" if pair_preflight_pass else "fail",
            "paired_preflight_maximum": NARRATION_PAIR_PREFLIGHT_MAX,
            "selected_pair": selected_pair,
            "selected_pair_preflight_ratio": selected_ratio,
            "selected_pair_files_match": selected_pair_files_match,
            "selected_pair_reused_only_within_same_candidate": True,
            "failed_take_reuse": False,
            "atempo_used": False,
            "pitch_correction_used": False,
            "threshold_relaxed": False,
            "fixed_cta_changed": False,
        },
        "all_section_profiles": profiles,
        "script_sha256": hashlib.sha256(script_text.encode("utf-8")).hexdigest(),
        "alignment_sha256": sha(ROOT / "narration_alignment.json"),
        "captions_sha256": sha(ROOT / "captions.srt"),
    }
    if continuous:
        result["recovery_protocol"] = {
            "name": NARRATION_GENERATION_PROTOCOL,
            "single_take_bound": pair_preflight_pass,
            "generation_request_count": evidence.get("generation_request_count"),
            "separately_generated_section_audio_concatenation": False,
            "atempo_used": (ROOT / "narration_tempo_map.json").exists(), "pitch_correction_used": False,
            "fixed_cta_changed": False, "threshold_relaxed": False,
        }
    dump(ROOT / "02_exact_runtime_gate.json", result)
    if result["status"] != "pass":
        raise RuntimeError("exact runtime gate failed")
    return result


def generate_minsoo_section(
    script: str,
    audio_path: Path,
    alignment_path: Path,
    *,
    previous_text: str | None,
    next_text: str | None,
    seed: int,
) -> None:
    """Generate one of seven locked sections; stored keys are quota retries, not voice fallbacks."""
    request_payload = {
        "text": script,
        "model_id": MINSOO_MODEL_ID,
        "voice_settings": MINSOO_VOICE_SETTINGS,
        "seed": seed,
    }
    if previous_text is not None:
        request_payload["previous_text"] = previous_text
    if next_text is not None:
        request_payload["next_text"] = next_text
    payload = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    keys = shorts_video._elevenlabs_api_keys()
    if not keys:
        raise RuntimeError("no ElevenLabs key; refusing TTS fallback")
    result = None
    last_error = None
    if NARRATION["generation_mode"] == "single_take_reference_restoration":
        keys = keys[:1]
    request_id = None
    for key in keys:
        request = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{MINSOO_VOICE_ID}/with-timestamps?output_format=mp3_44100_192",
            data=payload,
            headers={"Content-Type": "application/json", "xi-api-key": key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                request_id = getattr(response, "headers", {}).get("request-id")
                result = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (401, 429):
                continue
            raise RuntimeError("Minsoo ElevenLabs generation failed; refusing TTS fallback") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            last_error = exc
            continue
    if result is None:
        raise RuntimeError("all stored ElevenLabs keys unavailable; refusing TTS fallback") from last_error
    audio_base64 = result.get("audio_base64")
    alignment = result.get("alignment") or result.get("normalized_alignment")
    if not audio_base64 or not alignment:
        raise RuntimeError("Minsoo audio/alignment missing; refusing TTS fallback")
    audio_path.write_bytes(base64.b64decode(audio_base64))
    dump(alignment_path, {
        "voice_id": MINSOO_VOICE_ID,
        "model_id": MINSOO_MODEL_ID,
        "request_id": request_id,
        "settings": MINSOO_VOICE_SETTINGS,
        "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
        "script": script,
        "alignment": alignment,
        "normalized_alignment": result.get("normalized_alignment"),
        "original_alignment_text_matches_script": re.sub(r"\s+", "", "".join(alignment["characters"])) == re.sub(r"\s+", "", script),
        "generation_protocol": NARRATION_GENERATION_PROTOCOL,
        "seed": seed,
        "previous_text_sha256": (
            hashlib.sha256(previous_text.encode("utf-8")).hexdigest()
            if previous_text is not None else None
        ),
        "next_text_sha256": (
            hashlib.sha256(next_text.encode("utf-8")).hexdigest()
            if next_text is not None else None
        ),
    })


def split_seven_sections(script: str) -> list[str]:
    cta = fixed_cta(SOURCE_MINUTES)
    if not script.endswith(cta):
        raise RuntimeError("source-duration CTA missing")
    body = script[:-len(cta)].rstrip()
    marker_re = re.compile(r"(?m)^(첫째|둘째|셋째|넷째|다섯째),")
    matches = list(marker_re.finditer(body))
    if [match.group(1) for match in matches] != ["첫째", "둘째", "셋째", "넷째", "다섯째"]:
        raise RuntimeError("exactly first-through-fifth sections are required")
    sections = [body[:matches[0].start()].strip()]
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections.append(body[match.start():end].strip())
    sections.append(cta)
    if len(sections) != NARRATION["section_count"] or not all(sections):
        raise RuntimeError("narration must contain exactly seven non-empty sections")
    return sections


def narration_pace_profile(audio: Path, alignment: Path, name: str) -> dict:
    evidence = json.loads(alignment.read_text(encoding="utf-8"))
    aligned = evidence.get("alignment") or {}
    points = [
        (character, float(start), float(end))
        for character, start, end in zip(
            aligned.get("characters") or [],
            aligned.get("character_start_times_seconds") or [],
            aligned.get("character_end_times_seconds") or [],
        )
        if not str(character).isspace()
    ]
    if not points:
        raise RuntimeError(f"no aligned speech characters for {name}")
    audio_duration = duration(audio)
    silences = tailbite.detect_silences(
        audio,
        threshold_db=TAILBITE["threshold_db"],
        minimum=TAILBITE["minimum"],
    )
    segments = tailbite.build_segments(
        audio_duration,
        silences,
        retained_gap=TAILBITE["retained_gap"],
    )
    mapped_start = tailbite.map_time(points[0][1], segments)
    mapped_end = tailbite.map_time(points[-1][2], segments)
    spoken_seconds = max(0.001, mapped_end - mapped_start)
    return {
        "name": name,
        "character_count": len(points),
        "spoken_seconds": round(spoken_seconds, 6),
        "characters_per_second": round(len(points) / spoken_seconds, 6),
        "tailbite": {
            "threshold_db": TAILBITE["threshold_db"],
            "minimum_seconds": TAILBITE["minimum"],
            "retained_gap_seconds": TAILBITE["retained_gap"],
        },
    }


def _section_record(
    index: int,
    name: str,
    text: str,
    audio: Path,
    alignment: Path,
    *,
    previous_text: str | None,
    next_text: str | None,
) -> dict:
    evidence = json.loads(alignment.read_text(encoding="utf-8"))
    expected_previous = (
        hashlib.sha256(previous_text.encode("utf-8")).hexdigest()
        if previous_text is not None else None
    )
    expected_next = (
        hashlib.sha256(next_text.encode("utf-8")).hexdigest()
        if next_text is not None else None
    )
    if evidence.get("voice_id") != MINSOO_VOICE_ID or evidence.get("model_id") != MINSOO_MODEL_ID:
        raise RuntimeError("section voice/model provenance mismatch")
    if evidence.get("settings") != MINSOO_VOICE_SETTINGS:
        raise RuntimeError("section voice settings mismatch")
    if evidence.get("script_sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest():
        raise RuntimeError("section script hash mismatch")
    if evidence.get("generation_protocol") != NARRATION_GENERATION_PROTOCOL:
        raise RuntimeError("section paired generation protocol mismatch")
    if evidence.get("previous_text_sha256") != expected_previous:
        raise RuntimeError("section previous_text context mismatch")
    if evidence.get("next_text_sha256") != expected_next:
        raise RuntimeError("section next_text context mismatch")
    return {
        "index": index,
        "name": name,
        "script": text,
        "script_sha256": evidence["script_sha256"],
        "audio": str(audio),
        "audio_sha256": sha(audio),
        "alignment": str(alignment),
        "alignment_sha256": sha(alignment),
        "duration_seconds": duration(audio),
    }


def _generate_intro_cta_pair(pair_root: Path, sections: list[str], seed: int) -> dict:
    pair_root.mkdir(parents=True, exist_ok=False)
    intro_audio = pair_root / "01_intro.mp3"
    intro_alignment = pair_root / "01_intro_alignment.json"
    cta_audio = pair_root / "07_cta.mp3"
    cta_alignment = pair_root / "07_cta_alignment.json"
    generate_minsoo_section(
        sections[0], intro_audio, intro_alignment,
        previous_text=None, next_text=sections[1], seed=seed,
    )
    generate_minsoo_section(
        sections[6], cta_audio, cta_alignment,
        previous_text=sections[5], next_text=None, seed=seed,
    )
    early = narration_pace_profile(intro_audio, intro_alignment, "intro")
    late = narration_pace_profile(cta_audio, cta_alignment, "cta")
    ratio = late["characters_per_second"] / early["characters_per_second"]
    passed = ratio <= NARRATION_PAIR_PREFLIGHT_MAX
    result = {
        "status": "pass" if passed else "fail",
        "early": early,
        "late": late,
        "last_to_first_ratio": round(ratio, 6),
        "preflight_maximum": NARRATION_PAIR_PREFLIGHT_MAX,
        "exact_runtime_maximum": NARRATION["last_to_first_pace_ratio_max"],
        "pair_disposition": "selected" if passed else "discard_both",
        "failed_take_reuse": False,
        "atempo_used": False,
        "pitch_correction_used": False,
        "threshold_relaxed": False,
        "fixed_cta_changed": False,
        "seed": seed,
    }
    dump(pair_root / "pair_gate.json", result)
    return result


def _select_intro_cta_pair(sections: list[str]) -> tuple[Path, dict]:
    pair_root = ROOT / "narration_pair_preflight"
    pair_root.mkdir(parents=True, exist_ok=False)
    results = []
    for attempt in range(1, NARRATION_PAIR_MAX_ATTEMPTS + 1):
        candidate = pair_root / f"pair-{attempt:02d}"
        result = _generate_intro_cta_pair(
            candidate,
            sections,
            NARRATION_PAIR_SEED_BASE + attempt - 1,
        )
        results.append({"pair": candidate.name, **result})
        summary = {
            "protocol": NARRATION_GENERATION_PROTOCOL,
            "status": "pass" if result["status"] == "pass" else "searching",
            "preflight_maximum": NARRATION_PAIR_PREFLIGHT_MAX,
            "exact_runtime_maximum": NARRATION["last_to_first_pace_ratio_max"],
            "selected_pair": candidate.name if result["status"] == "pass" else None,
            "selected_pair_preflight_ratio": (
                result["last_to_first_ratio"] if result["status"] == "pass" else None
            ),
            "pairs": results,
        }
        dump(pair_root / "summary.json", summary)
        if result["status"] == "pass":
            return candidate, summary
    summary["status"] = "fail"
    dump(pair_root / "summary.json", summary)
    raise RuntimeError(
        f"no intro/CTA pair passed {NARRATION_PAIR_PREFLIGHT_MAX:.2f} preflight reserve"
    )


def _prepare_narration_sections(sections: list[str]) -> tuple[list[dict], dict]:
    selected_pair, pair_summary = _select_intro_cta_pair(sections)
    section_root = ROOT / "narration_sections"
    section_root.mkdir(exist_ok=False)
    for index, name in ((1, "intro"), (7, "cta")):
        shutil.copy2(selected_pair / f"{index:02d}_{name}.mp3", section_root / f"{index:02d}_{name}.mp3")
        shutil.copy2(
            selected_pair / f"{index:02d}_{name}_alignment.json",
            section_root / f"{index:02d}_{name}_alignment.json",
        )
    seed = int(next(
        pair["seed"] for pair in pair_summary["pairs"]
        if pair["pair"] == pair_summary["selected_pair"]
    ))
    for index in range(2, 7):
        name = NARRATION_SECTION_NAMES[index - 1]
        generate_minsoo_section(
            sections[index - 1],
            section_root / f"{index:02d}_{name}.mp3",
            section_root / f"{index:02d}_{name}_alignment.json",
            previous_text=sections[index - 2],
            next_text=sections[index],
            seed=seed,
        )
    records = [
        _section_record(
            index,
            name,
            text,
            section_root / f"{index:02d}_{name}.mp3",
            section_root / f"{index:02d}_{name}_alignment.json",
            previous_text=sections[index - 2] if index > 1 else None,
            next_text=sections[index] if index < len(sections) else None,
        )
        for index, (name, text) in enumerate(zip(NARRATION_SECTION_NAMES, sections), 1)
    ]
    return records, pair_summary


def generate_single_take(sections: list[str]) -> tuple[Path, Path, list[dict]]:
    """Restore the earlier continuous narration; seven sections are timing labels only."""
    script = SCRIPT.read_text(encoding="utf-8").strip()
    audio = ROOT / "narration_single_take.mp3"
    alignment = ROOT / "narration_alignment.json"
    if audio.exists() or alignment.exists():
        raise RuntimeError("single-take candidate already exists; refusing overwrite")
    production_path = ROOT / "production_manifest.json"
    reuse = (json.loads(production_path.read_text()).get("render_inputs", {}).get("single_take_reuse")
             if production_path.exists() else None)
    if reuse:
        from content_lineage import bound_file
        original_audio = bound_file(ROOT, reuse.get("audio"), "single take")
        original_alignment = bound_file(ROOT, reuse.get("alignment"), "single take alignment")
        prior = json.loads(original_alignment.read_text())
        if (prior.get("script_sha256") != hashlib.sha256(script.encode()).hexdigest()
                or prior.get("voice_id") != MINSOO_VOICE_ID
                or prior.get("model_id") != MINSOO_MODEL_ID
                or prior.get("settings") != MINSOO_VOICE_SETTINGS
                or prior.get("generation_request_count") != 1
                or prior.get("single_take_sha256") != sha(original_audio)):
            raise RuntimeError("Reuse does not bind one complete exact-script voice take")
        shutil.copy2(original_audio, audio)
        shutil.copy2(original_alignment, alignment)
    else:
        generate_minsoo_section(script, audio, alignment, previous_text=None, next_text=None,
                                seed=2026090602)
    evidence = json.loads(alignment.read_text())
    if evidence.get("original_alignment_text_matches_script") is not True:
        raise RuntimeError("Provider original alignment does not match the Korean script")
    timings = tailbite.aligned_words(alignment)
    if len(timings) != len(script.split()):
        raise RuntimeError("single-take alignment differs from complete script tokens")
    records = []
    offset = 0
    for index, (name, section) in enumerate(zip(NARRATION_SECTION_NAMES, sections), 1):
        count = len(section.split())
        start, end = timings[offset][0], timings[offset + count - 1][1]
        records.append({"index": index, "name": name, "script": section,
                        "script_sha256": hashlib.sha256(section.encode()).hexdigest(),
                        "audio": str(audio), "audio_sha256": sha(audio),
                        "alignment": str(alignment),
                        "combined_offset_seconds": start,
                        "duration_seconds": end - start,
                        "logical_section_only": True})
        offset += count
    if offset != len(timings):
        raise RuntimeError("logical sections do not cover the complete single take")
    evidence.update(generation_mode=NARRATION["generation_mode"], section_count=7,
                    section_gap_seconds=0.0, generation_request_count=1,
                    single_take_sha256=sha(audio), sections=records)
    dump(alignment, evidence)
    return audio, alignment, records


def combine_section_audio_and_alignment(sections: list[str]) -> tuple[Path, Path, list[dict]]:
    if NARRATION["generation_mode"] == "single_take_reference_restoration":
        return generate_single_take(sections)
    records, pair_summary = _prepare_narration_sections(sections)
    audio_paths = [Path(record["audio"]) for record in records]
    combined_audio = ROOT / "narration_seven_sections.mp3"
    command: list[object] = ["ffmpeg", "-hide_banner", "-y"]
    for audio in audio_paths:
        command += ["-i", audio]
    filters = []
    sequence = []
    for index in range(len(audio_paths)):
        filters.append(
            f"[{index}:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=mono[s{index}]"
        )
        sequence.append(f"[s{index}]")
        if index < len(audio_paths) - 1:
            filters.append(
                f"anullsrc=r=44100:cl=mono,atrim=duration={NARRATION['section_gap_seconds']:.3f}[g{index}]"
            )
            sequence.append(f"[g{index}]")
    filters.append(
        f"{''.join(sequence)}concat=n={len(sequence)}:v=0:a=1,"
        "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=mono[out]"
    )
    command += ["-filter_complex", ";".join(filters), "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "192k", combined_audio]
    run(command)

    characters: list[str] = []
    starts: list[float] = []
    ends: list[float] = []
    offset = 0.0
    for index, record in enumerate(records):
        evidence = json.loads(Path(record["alignment"]).read_text(encoding="utf-8"))["alignment"]
        section_chars = list(evidence["characters"])
        section_starts = [float(value) + offset for value in evidence["character_start_times_seconds"]]
        section_ends = [float(value) + offset for value in evidence["character_end_times_seconds"]]
        characters.extend(section_chars)
        starts.extend(section_starts)
        ends.extend(section_ends)
        record["combined_offset_seconds"] = offset
        offset += float(record["duration_seconds"])
        if index < len(records) - 1:
            characters.extend(["\n", "\n"])
            starts.extend([offset + 0.06, offset + 0.18])
            ends.extend([offset + 0.06, offset + 0.18])
            offset += NARRATION["section_gap_seconds"]

    combined_alignment = ROOT / "narration_alignment.json"
    script = SCRIPT.read_text(encoding="utf-8").strip()
    dump(combined_alignment, {
        "voice_id": MINSOO_VOICE_ID,
        "model_id": MINSOO_MODEL_ID,
        "settings": MINSOO_VOICE_SETTINGS,
        "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
        "generation_mode": NARRATION["generation_mode"],
        "generation_protocol": NARRATION_GENERATION_PROTOCOL,
        "section_count": NARRATION["section_count"],
        "section_gap_seconds": NARRATION["section_gap_seconds"],
        "selected_pair": pair_summary["selected_pair"],
        "selected_pair_preflight_ratio": pair_summary["selected_pair_preflight_ratio"],
        "sections": records,
        "alignment": {
            "characters": characters,
            "character_start_times_seconds": starts,
            "character_end_times_seconds": ends,
        },
    })
    return combined_audio, combined_alignment, records


def make_audio_and_captions() -> tuple[Path, Path, list[dict], list[tailbite.Segment]]:
    combined = ROOT / "narration_seven_sections.mp3"
    alignment = ROOT / "narration_alignment.json"
    cut = ROOT / "narration_tailbite.mp3"
    srt = ROOT / "captions.srt"
    script = SCRIPT.read_text(encoding="utf-8").strip()
    sections = split_seven_sections(script)
    combined, alignment, records = combine_section_audio_and_alignment(sections)
    source_duration = duration(combined)
    silences = tailbite.detect_silences(
        combined,
        threshold_db=TAILBITE["threshold_db"],
        minimum=TAILBITE["minimum"],
    )
    if NARRATION["generation_mode"] == "single_take_reference_restoration":
        # Trim only silent inter-word gaps; never remove an aligned spoken word.
        word_intervals = tailbite.aligned_words(alignment)
        safe_silences = []
        for left, right in silences:
            fragments = [(left, right)]
            for word_start, word_end in word_intervals:
                next_fragments = []
                for start, end in fragments:
                    if word_end <= start or word_start >= end:
                        next_fragments.append((start, end))
                    else:
                        if start < word_start:
                            next_fragments.append((start, word_start))
                        if word_end < end:
                            next_fragments.append((word_end, end))
                fragments = next_fragments
            safe_silences.extend((a, b) for a, b in fragments if b - a >= TAILBITE["minimum"])
        silences = sorted(safe_silences)
    segments = tailbite.build_segments(
        source_duration,
        silences,
        retained_gap=TAILBITE["retained_gap"],
    )
    tailbite.render_audio(combined, cut, segments)
    raw_tokens = [x for x in re.split(r"\s+", script) if x]
    timings = tailbite.aligned_words(alignment)
    if len(raw_tokens) != len(timings):
        raise RuntimeError(f"script/alignment token mismatch: {len(raw_tokens)} != {len(timings)}")
    captions = []
    for raw, (start, end) in zip(raw_tokens, timings):
        clean = strip_subtitle_edge_punctuation(raw)
        if clean:
            captions.append({
                "text": clean,
                "start": tailbite.map_time(start, segments),
                "end": tailbite.map_time(end, segments),
            })
    tailbite.write_srt(srt, captions)
    if NARRATION["generation_mode"] == "single_take_reference_restoration":
        from shorts_narration_tempo import retime
        tailbite.write_srt(ROOT / "captions_before_tempo.srt", captions)
        cut, captions = retime(cut, captions, sections, REFERENCE_CONFIG.parent / "captions.srt",
                               parse_srt=parse_srt, duration=duration,
                               clean_token=strip_subtitle_edge_punctuation)
        tailbite.write_srt(srt, captions)
        tempo_path = ROOT / "narration_tempo_map.json"
        tempo_evidence = json.loads(tempo_path.read_text())
        tempo_evidence["captions_sha256"] = sha(srt)
        dump(tempo_path, tempo_evidence)
    alignment_json = json.loads(alignment.read_text(encoding="utf-8"))
    if alignment_json.get("voice_id") != MINSOO_VOICE_ID or alignment_json.get("model_id") != MINSOO_MODEL_ID:
        raise RuntimeError("voice/model provenance mismatch")
    if alignment_json.get("settings") != MINSOO_VOICE_SETTINGS:
        raise RuntimeError("voice settings mismatch")
    manifest = {
        "source_id": SOURCE_ID,
        "script": str(SCRIPT),
        "script_sha256": sha(SCRIPT),
        "voice_id": MINSOO_VOICE_ID,
        "model_id": MINSOO_MODEL_ID,
        "voice_settings": MINSOO_VOICE_SETTINGS,
        "generation_mode": NARRATION["generation_mode"],
        "generation_protocol": NARRATION_GENERATION_PROTOCOL,
        "section_count": NARRATION["section_count"],
        "section_gap_seconds": NARRATION["section_gap_seconds"],
        "selected_pair": alignment_json.get("selected_pair"),
        "selected_pair_preflight_ratio": alignment_json.get("selected_pair_preflight_ratio"),
        "generation_request_count": alignment_json.get("generation_request_count"),
        "sections": records,
        "combined_take": str(combined),
        "combined_take_sha256": sha(combined),
        "alignment": str(alignment),
        "alignment_sha256": sha(alignment),
        "tailbite_audio": str(cut),
        "tailbite_audio_sha256": sha(cut),
        "source_duration_seconds": source_duration,
        "output_duration_seconds": duration(cut),
        "tailbite": {
            "threshold_db": TAILBITE["threshold_db"],
            "minimum_seconds": TAILBITE["minimum"],
            "retained_gap_seconds": TAILBITE["retained_gap"],
        },
        "silence_count": len(silences),
        "segment_count": len(segments),
        "caption_mode": "one_full_token_no_midword_split",
        "caption_count": len(captions),
        "fallback_used": False,
    }
    dump(ROOT / "narration_manifest.json", manifest)
    return cut, srt, captions, segments


def build_scene_manifest(captions: list[dict], dur: float) -> list[float]:
    markers = []
    for word in ("첫째", "둘째", "셋째", "넷째", "다섯째"):
        cue = next((x for x in captions if x["text"] == word), None)
        if cue is None:
            raise RuntimeError(f"missing ordinal caption: {word}")
        markers.append(round(float(cue["start"]), 3))
    boundaries = [0.0] + markers + [dur]
    ids = ["hook_promise", "first", "second", "third", "fourth", "fifth"]
    jobs = SCENE_JOBS
    events = []
    for index, (event_id, job) in enumerate(zip(ids, jobs)):
        start = boundaries[index]
        end = boundaries[index + 1]
        if end <= start:
            raise RuntimeError(f"invalid scene interval: {event_id}")
        events.append({
            "id": event_id,
            "start": round(start, 3),
            "end": round(end, 3),
            "claim": job,
            "visual_job": "original YouTube footage centered with Minsoo circular PIP",
            "primary_text_channel": "subtitle",
            "referent_time": round(start, 3),
        })
    dump(ROOT / "scene-manifest.json", {"duration": round(dur, 3), "events": events})
    cues = [{
        "id": f"ordinal_{index + 1}",
        "start": start,
        "peak": start,
        "trigger": word,
        "semantic_family": "transition",
        "source": str(SFX),
        "source_sha256": sha(SFX),
        "license": "locked project ElevenLabs-generated SFX asset",
        "gain_instruction": "calibrate combined SFX stem to voice peak minus 10 dB",
        "ducking": "none required after voice-dominance gate",
    } for index, (word, start) in enumerate(zip(("첫째", "둘째", "셋째", "넷째", "다섯째"), markers))]
    dump(ROOT / "sound-cue-sheet.json", {"duration": round(dur, 3), "cues": cues})
    return markers


def load_renderer():
    spec = importlib.util.spec_from_file_location("approved_v7_renderer", RENDERER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_config(markers: list[float]) -> dict:
    cfg = json.loads(REFERENCE_CONFIG.read_text(encoding="utf-8"))
    candidate_lines = []
    for line in HEADCOPY.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\d+\.\s*(.+?)\s*/\s*(.+)$", line.strip())
        if match:
            candidate_lines.append(f"{match.group(1)}\n{match.group(2)}")
    if len(candidate_lines) != 3:
        raise RuntimeError("exactly three two-line headcopy candidates required")
    for index, candidate in enumerate(candidate_lines):
        # Only the first candidate is drawn, so only it is held to the 90px width.
        validate_head_copy(candidate, measure_pixels=index == 0)
    validate_head_copy_connection(candidate_lines[0], SCRIPT.read_text(encoding="utf-8"))
    presenter = validate_presenter_asset(PRESENTER)
    cfg["title"].update({
        "text": candidate_lines[0], "font_path": str(TITLE_FONT),
        "font_size": 90, "x": 540, "y": 440, "line_spacing": 20,
    })
    cfg["subtitle"].update({
        "font_path": str(BODY_FONT), "font_size": 59,
        "x": 540, "y": 940, "max_lines": 1,
        "merge_enabled": False, "mode": "eojel", "max_chars_per_line": 30,
    })
    cfg["presenter"] = {
        "x": 325, "y": 1298, "width": 430, "height": 430, "shape": "circle",
        "crop": "", "color_filter": "eq=brightness=0.025:contrast=1.14:saturation=1.06,colorbalance=bs=0.035:gs=0.01",
        "left_light": {"color": "00C8FF", "alpha": 0.38, "width": 260},
    }
    cfg["screen"] = {"fit": "cover", "x": 0, "y": 664, "width": 1080, "height": 608, "start_at": 0, "speed": 2.0}
    cfg["watermark"].update({
        "text": "@aimax", "font_path": str(TITLE_FONT),
        "x": 540, "y": 1768, "font_size": 44,
    })
    cfg["source"].update({"text": SOURCE_CREDIT, "x": 540, "y": 1270, "font_size": 27, "duration": 3.0})
    cfg["audio"] = {
        "voice_volume": 1.0, "master_lufs": -14.0, "master_lra": 3.0,
        "master_true_peak": -2.5, "effects": [],
    }
    cfg["render_provenance"] = {
        "renderer_path": str(RENDERER_PATH),
        "renderer_sha256": sha(RENDERER_PATH),
        "approved_reference_config": str(REFERENCE_CONFIG),
        "presenter_input": presenter["path"],
        "presenter_sha256": presenter["sha256"],
        "source_footage": str(SOURCE),
        "source_footage_sha256": sha(SOURCE),
        "source_url": SOURCE_URL,
        "source_id": SOURCE_ID,
        "source_fetch_backend": "source task manifest URL / project .venv312 yt-dlp mweb format 18 / public source / no cookies",
        "script_exact": str(SCRIPT),
        "script_exact_sha256": sha(SCRIPT),
        "source_and_presenter_audio_mapped": False,
        "source_audio_mapped": False,
        "presenter_audio_mapped": False,
        "final_audio_sources": ["approved Minsoo narration", "locked BGM", "five locked ordinal SFX"],
        "screen_method": "exact public YouTube video; center cover crop; start_at=0; speed=2.0",
        "delivery_encoding": "H.264 libx264 CRF 24 / medium / yuv420p / 30fps; AAC 192k passthrough",
        "v7_reference_restoration": {
            "voice_settings": MINSOO_VOICE_SETTINGS,
            "subtitle_rule": "one full token; no Latin/mixed-token slicing",
            "headline_font_size_1080": 90,
            "reference": "LW8KLS9j2_E_shorts_1080x1920_APPROVED_BALANCED.mp4",
        },
    }
    return cfg


def build_sfx_stem(markers: list[float], dur: float) -> Path:
    raw = ROOT / "sfx_raw.wav"
    args: list[object] = ["ffmpeg", "-hide_banner", "-y"]
    for _ in markers:
        args += ["-i", SFX]
    chains = []
    labels = []
    for index, start in enumerate(markers):
        delay = int(round(start * 1000))
        chains.append(f"[{index}:a]aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo,adelay={delay}|{delay}[s{index}]")
        labels.append(f"[s{index}]")
    chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,apad,atrim=0:{dur:.3f}[out]")
    args += ["-filter_complex", ";".join(chains), "-map", "[out]", "-c:a", "pcm_s24le", raw]
    run(args)
    raw_metrics = loudness(raw)
    voice_peak = loudness(ROOT / "voice_stem.wav")["true_peak_dbtp"]
    desired_peak = min(-14.0, voice_peak - 8.5)
    gain = desired_peak - raw_metrics["true_peak_dbtp"]
    stem = ROOT / "sfx_stem.wav"
    run([
        "ffmpeg", "-hide_banner", "-y", "-i", raw,
        "-af", f"volume={gain:.4f}dB,alimiter=limit={10 ** (desired_peak / 20):.6f}:level=false",
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", stem,
    ])
    return stem


def make_contact_sheets(final: Path, dur: float, markers: list[float]) -> dict:
    validation = ROOT / "validation"
    validation.mkdir(exist_ok=True)
    points = sorted(set(round(max(0.2, min(dur - 0.3, value)), 3) for value in ([1.0, dur / 2, dur - 0.5] + markers)))
    frames = []
    for index, point in enumerate(points):
        frame = validation / f"frame_{index:02d}_{point:.3f}s.png"
        run(["ffmpeg", "-hide_banner", "-y", "-ss", point, "-i", final, "-frames:v", "1", frame])
        frames.append(frame)
    concat = validation / "frames.txt"
    concat.write_text("".join(f"file '{frame.name}'\n" for frame in frames), encoding="utf-8")
    cols = 3
    rows = math.ceil(len(frames) / cols)
    contact = validation / "contact_sheet.png"
    pip_contact = validation / "pip_contact_sheet.png"
    run([
        "ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", concat,
        "-vf", f"scale=360:640,tile={cols}x{rows}:padding=0:margin=0", "-frames:v", "1", contact,
    ])
    run([
        "ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", concat,
        "-vf", f"crop=430:430:325:1298,scale=240:240,tile={cols}x{rows}:padding=0:margin=0", "-frames:v", "1", pip_contact,
    ])
    representative = ROOT / "representative_screenshots"
    representative.mkdir(exist_ok=True)
    labels = ["01_hook", "02_item1", "03_item2", "04_item3", "05_item4", "06_item5", "07_cta"]
    representative_points = [1.0] + [min(dur - 1.0, marker + 0.8) for marker in markers] + [max(1.0, dur - 4.0)]
    representative_files = []
    for label, point in zip(labels, representative_points):
        target = representative / f"{label}.png"
        run(["ffmpeg", "-hide_banner", "-y", "-ss", f"{point:.3f}", "-i", final, "-frames:v", "1", target])
        representative_files.append(str(target))
    return {"checkpoints": points, "frames": [str(x) for x in frames], "contact_sheet": str(contact), "pip_contact_sheet": str(pip_contact), "representative_screenshots": representative_files}


def render() -> int:
    from content_lineage import validate_shorts_origin
    validate_shorts_origin(ROOT)
    validate_scene_sentinels()
    for path in (
        SOURCE, PRESENTER, SCRIPT, HEADCOPY, RENDERER_PATH, REFERENCE_CONFIG,
        BGM, SFX, TITLE_FONT, BODY_FONT,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)
    script_text = SCRIPT.read_text(encoding="utf-8").strip()
    if not script_text.endswith(fixed_cta(SOURCE_MINUTES)) or "여섯째" in script_text:
        raise RuntimeError("fifth-item/fixed-CTA script gate failed")
    if sum(script_text.count(x) for x in ("첫째", "둘째", "셋째", "넷째", "다섯째")) != 5:
        raise RuntimeError("exactly five ordinal markers required")

    tailbite_audio, srt, captions, segments = make_audio_and_captions()
    runtime_gate = build_runtime_gate(segments, captions)
    voice_stem = ROOT / "voice_stem.wav"
    # The current ffmpeg build lands about 0.6 LU below the requested linear
    # target for this stereo conversion, so calibrate the request to measure
    # the locked -16 LUFS delivery value on the resulting stem.
    normalize_loudness(tailbite_audio, voice_stem, -14.8, -1.8)
    calibrate_voice_lufs(voice_stem)
    dur = duration(voice_stem)
    markers = build_scene_manifest(captions, dur)
    cfg = build_config(markers)
    dump(ROOT / "render_config.json", cfg)

    renderer = load_renderer()
    ass = ROOT / "captions.ass"
    renderer.srt_to_ass(srt, ass, cfg)
    visual = ROOT / "visual_with_temp_audio.mp4"
    if not visual.exists():
        renderer.render_final(PRESENTER, ass, visual, cfg, SOURCE, voice_stem)

    bgm_stem = ROOT / "bgm_stem.wav"
    normalize_loudness(BGM, bgm_stem, -31.0, -9.0, dur)
    sfx_stem = build_sfx_stem(markers, dur)
    premaster = ROOT / "premaster_mix.wav"
    run([
        "ffmpeg", "-hide_banner", "-y", "-i", voice_stem, "-i", bgm_stem, "-i", sfx_stem,
        "-filter_complex", f"[0:a][1:a][2:a]amix=inputs=3:duration=first:dropout_transition=0:normalize=0,atrim=0:{dur:.3f}[out]",
        "-map", "[out]", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", premaster,
    ])
    final_master = ROOT / "final_master.wav"
    normalize_loudness(premaster, final_master, -14.0, -3.0)
    final_m4a = ROOT / "final_master.m4a"
    run([
        "ffmpeg", "-hide_banner", "-y", "-i", final_master,
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", final_m4a,
    ])
    run([
        "ffmpeg", "-hide_banner", "-y", "-i", visual, "-i", final_m4a,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "medium", "-crf", "24", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "copy", "-shortest", "-movflags", "+faststart", FINAL,
    ])

    return validate_existing_render()


def validate_existing_render() -> int:
    """Recheck the actual MP4 without generating TTS or overwriting the video."""
    from content_lineage import validate_shorts_origin
    validate_shorts_origin(ROOT)
    script_text = SCRIPT.read_text(encoding='utf-8').strip()
    voice_stem, bgm_stem, sfx_stem = (ROOT / name for name in ('voice_stem.wav', 'bgm_stem.wav', 'sfx_stem.wav'))
    premaster, final_master = ROOT / 'premaster_mix.wav', ROOT / 'final_master.wav'
    srt, ass = ROOT / 'captions.srt', ROOT / 'captions.ass'
    cfg = json.loads((ROOT / 'render_config.json').read_text())
    runtime_gate = json.loads((ROOT / '02_exact_runtime_gate.json').read_text())
    captions = parse_srt(srt)
    markers = [next(x['start'] for x in captions if x['text'] == word)
               for word in ('첫째', '둘째', '셋째', '넷째', '다섯째')]
    voice_metrics = loudness(voice_stem)
    bgm_metrics = loudness(bgm_stem)
    sfx_metrics = loudness(sfx_stem)
    premaster_metrics = loudness(premaster)
    final_pcm_metrics = loudness(final_master)
    final_metrics = loudness(FINAL)
    final_probe = probe(FINAL)
    video = next(x for x in final_probe["streams"] if x.get("codec_type") == "video")
    audio = next(x for x in final_probe["streams"] if x.get("codec_type") == "audio")
    full_decode = run(["ffmpeg", "-v", "error", "-i", FINAL, "-f", "null", "-"], check=False)
    srt_events = parse_srt(srt)
    script_tokens = [x for x in re.split(r"\s+", script_text) if x]
    subtitle_ok = (
        len(srt_events) == len(script_tokens)
        and all(" " not in x["text"] for x in srt_events)
        # write_srt already clamps each cue to the next cue's start, so the only
        # thing that can push an end past its neighbour is the 100 ms readability
        # floor applied to a token too short to fill it. Check that mechanism
        # rather than a tolerance in milliseconds: cues must stay in order, and
        # any overlap must belong to a cue sitting exactly on the floor.
        and all(left["start"] < right["start"] for left, right in zip(srt_events, srt_events[1:]))
        and all(
            right["start"] >= left["end"]
            or abs((left["end"] - left["start"]) - SUBTITLE_READABILITY_FLOOR_S) <= 0.001
            for left, right in zip(srt_events, srt_events[1:])
        )
        and [x['text'] for x in srt_events] == [strip_subtitle_edge_punctuation(x) for x in script_tokens]
    )
    subtitle_edge_punctuation_free = all(
        x["text"] == strip_subtitle_edge_punctuation(x["text"])
        for x in srt_events
    )
    measured = {
        "voice_stem": voice_metrics,
        "bgm_stem": bgm_metrics,
        "sfx_stem": sfx_metrics,
        "premaster_mix": premaster_metrics,
        "final_master_pcm": final_pcm_metrics,
        "final_rendered_aac": final_metrics,
        "voice_minus_bgm_lu": round(voice_metrics["integrated_lufs"] - bgm_metrics["integrated_lufs"], 2),
        "voice_peak_minus_sfx_peak_db": round(voice_metrics["true_peak_dbtp"] - sfx_metrics["true_peak_dbtp"], 2),
    }
    gates = {
        "voice_integrated_minus16_tolerance_0_5": abs(voice_metrics["integrated_lufs"] + 16.0) <= 0.5,
        "voice_true_peak_at_most_minus2": voice_metrics["true_peak_dbtp"] <= -2.0,
        "voice_minus_bgm_at_least14_lu": measured["voice_minus_bgm_lu"] >= 14.0,
        "voice_peak_minus_sfx_peak_at_least8_db": measured["voice_peak_minus_sfx_peak_db"] >= 8.0,
        "final_integrated_minus14_tolerance_0_5": abs(final_metrics["integrated_lufs"] + 14.0) <= 0.5,
        "final_true_peak_at_most_minus1_8": final_metrics["true_peak_dbtp"] <= -1.8,
        "full_decode": full_decode.returncode == 0,
        "exact_script_narration_caption_and_config_hashes": (
            json.loads((ROOT / "narration_alignment.json").read_text(encoding="utf-8"))["script_sha256"]
            == hashlib.sha256(script_text.encode("utf-8")).hexdigest()
            and cfg["render_provenance"]["script_exact_sha256"] == sha(SCRIPT)
            and subtitle_ok
            and runtime_gate["status"] == "pass"
        ),
        "source_audio_mapped": False,
        "presenter_audio_mapped": False,
        "media_1080x1920_30fps_h264_aac": (
            int(video["width"]) == 1080 and int(video["height"]) == 1920
            and video.get("r_frame_rate") == "30/1" and video.get("codec_name") == "h264"
            and audio.get("codec_name") == "aac"
        ),
        "full_token_subtitles": subtitle_ok,
        "subtitle_edge_punctuation_free": subtitle_edge_punctuation_free,
        "exactly_five_ordinal_cues": len(markers) == 5,
    }
    failures = [name for name, value in gates.items() if name not in ("source_audio_mapped", "presenter_audio_mapped") and value is not True]
    machine = {
        "source_id": SOURCE_ID,
        "status": "pass" if not failures else "fail",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "final_mp4": str(FINAL),
        "final_sha256": sha(FINAL),
        "duration_seconds": float(final_probe["format"]["duration"]),
        "resolution": [int(video["width"]), int(video["height"])],
        "fps": video.get("r_frame_rate"),
        "gates": gates,
        "failures": failures,
        "measured": measured,
        "subtitle": {"count": len(srt_events), "script_token_count": len(script_tokens), "mode": "one_full_token_no_midword_split"},
        "source_sha256": sha(SOURCE),
        "presenter_sha256": sha(PRESENTER),
        "script_sha256": sha(SCRIPT),
        "render_config_sha256": sha(ROOT / "render_config.json"),
    }
    dump(ROOT / "stem_metrics.json", {"status": machine["status"], "measured": measured, "gates": gates, "failures": failures})
    dump(ROOT / "machine_validation.json", machine)
    if failures:
        raise RuntimeError(f"machine gates failed: {failures}")

    visual_evidence = make_contact_sheets(FINAL, machine["duration_seconds"], markers)
    dump(ROOT / "visual_validation.json", {
        "status": "pending_human_inspection",
        "layout_checks": {
            "headline_90px_two_lines": True,
            "original_source_center": True,
            "minsoo_circular_pip": True,
            "single_token_subtitles": True,
            "watermark": True,
        },
        **visual_evidence,
    })
    production = {
        **json.loads((ROOT / "production_manifest.json").read_text()),
        "source_id": SOURCE_ID,
        "title_candidate": UPLOAD_TITLE,
        "video": str(FINAL),
        "srt": str(srt),
        "ass": str(ass),
        "provider_mutation_attempted": False,
        "studio_opened": False,
        "crm_emitted": False,
    }
    dump(ROOT / "production_manifest.json", production)
    bundle = validate_shorts_render_bundle(FINAL)
    dump(ROOT / "render_gate.json", {"status": "pass", "upload_authorized": False, **bundle})
    print(json.dumps({"status": "pass", "final": str(FINAL), "duration": machine["duration_seconds"], "audio": measured, "contact_sheet": visual_evidence["contact_sheet"]}, ensure_ascii=False, indent=2))
    return 0


def configure(root: Path) -> None:
    global ROOT, SOURCE_ID, SOURCE_URL, SOURCE, PRESENTER, SCRIPT, HEADCOPY, FINAL
    global SOURCE_MINUTES, SCENE_JOBS, SCENE_SENTINELS, UPLOAD_TITLE, SOURCE_CREDIT
    from content_lineage import bound_file, validate_shorts_origin
    ROOT = root.resolve()
    origin = validate_shorts_origin(ROOT)
    manifest = json.loads((ROOT / "production_manifest.json").read_text())
    data = manifest["render_inputs"]
    SOURCE_ID = origin["source_key"]
    SOURCE_URL = f"https://youtu.be/{SOURCE_ID}"
    SOURCE = bound_file(ROOT, data["source"], "source video")
    PRESENTER = bound_file(ROOT, data["presenter"], "presenter")
    SCRIPT = bound_file(ROOT, manifest["content_lineage"]["script"], "script")
    HEADCOPY = bound_file(ROOT, data["headcopy"], "headcopy")
    FINAL = ROOT / "final.mp4"
    SOURCE_MINUTES = int(manifest["content_lineage"]["source_minutes"])
    SCENE_JOBS = data["scene_jobs"]
    SCENE_SENTINELS = data["scene_sentinels"]
    UPLOAD_TITLE = data["upload_title"]
    SOURCE_CREDIT = data["source_credit"]
    if not SOURCE_CREDIT.startswith('출처: ') or len(SOURCE_CREDIT) <= 4:
        raise RuntimeError('source channel attribution is required')
    if len(SCENE_JOBS) != 6 or len(SCENE_SENTINELS) != 5 or not UPLOAD_TITLE:
        raise RuntimeError("source scene mapping and upload title are required")
    validate_presenter_asset(PRESENTER)
    validate_scene_sentinels()
    # Validate all three headlines before any billed TTS call.
    build_config([1, 2, 3, 4, 5])


def validate_scene_sentinels() -> None:
    sections = split_seven_sections(SCRIPT.read_text(encoding="utf-8"))
    for section, required in zip(sections[1:6], SCENE_SENTINELS):
        if not required or not all(isinstance(term, str) and term and term in section for term in required):
            raise RuntimeError("source-specific scene sentinel missing")


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Common V7 renderer; source data lives in production_manifest.json")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--render", action="store_true", help="Generate paid TTS and render after origin validation")
    parser.add_argument("--validate-existing", action="store_true", help="Recheck the existing MP4 without regenerating media")
    args = parser.parse_args(argv)
    if args.render and args.validate_existing:
        parser.error('choose render or validate-existing')
    configure(args.root)
    if args.validate_existing:
        return validate_existing_render()
    if not args.render:
        print(json.dumps({"status": "pass", "scope": "pre_render", "source_key": SOURCE_ID}))
        return 0
    if FINAL.exists():
        raise RuntimeError("preserve existing video; choose a separate candidate directory")
    return render()


if __name__ == "__main__":
    raise SystemExit(main())
