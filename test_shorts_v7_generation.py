import base64
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

import content_production_policy as policy


BUILDER_PATH = (
    Path(__file__).resolve().parent
    / "shorts_v7_builder.py"
)


def load_builder():
    spec = importlib.util.spec_from_file_location("test_shorts_v7_builder", BUILDER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SOURCE_MINUTES = 1  # Fixture source duration; production requires manifest binding.
    return module


def test_source_window_keeps_locked_speed_and_original_bounds():
    module = load_builder()
    assert module.validate_source_window(40, 2056, 60) == 40
    assert module.validate_source_window(0, 120, 60) == 0
    for start, source_seconds, output_seconds in [
        (-1, 200, 60), (200, 200, 0), (90, 200, 60),
        (float('nan'), 200, 60), (40, 200, float('inf')),
    ]:
        with pytest.raises(RuntimeError):
            module.validate_source_window(start, source_seconds, output_seconds)


def fake_section_writer(module, calls):
    def write(script, audio_path, alignment_path, *, previous_text, next_text, seed):
        calls.append({
            "script": script,
            "audio": audio_path,
            "previous_text": previous_text,
            "next_text": next_text,
            "seed": seed,
        })
        audio_path.write_bytes(f"{audio_path.parent.name}:{audio_path.name}".encode())
        alignment_path.write_text(json.dumps({
            "voice_id": policy.MINSOO_VOICE_ID,
            "model_id": policy.MINSOO_MODEL_ID,
            "settings": policy.MINSOO_VOICE_SETTINGS,
            "script": script,
            "script_sha256": hashlib.sha256(script.encode()).hexdigest(),
            "generation_protocol": module.NARRATION_GENERATION_PROTOCOL,
            "seed": seed,
            "previous_text_sha256": (
                hashlib.sha256(previous_text.encode()).hexdigest()
                if previous_text is not None else None
            ),
            "next_text_sha256": (
                hashlib.sha256(next_text.encode()).hexdigest()
                if next_text is not None else None
            ),
            "alignment": {
                "characters": list(script),
                "character_start_times_seconds": [0.0] * len(script),
                "character_end_times_seconds": [1.0] * len(script),
            },
        }), encoding="utf-8")

    return write


def test_pair_preflight_discards_both_then_generates_middle_sections(monkeypatch, tmp_path):
    builder = load_builder()
    builder.ROOT = tmp_path
    calls = []
    sections = ["intro", "first", "second", "third", "fourth", "fifth", "fixed cta"]
    monkeypatch.setattr(builder, "generate_minsoo_section", fake_section_writer(builder, calls))
    monkeypatch.setattr(builder, "duration", lambda _path: 1.0)

    def profile(audio, _alignment, name):
        pair = audio.parent.name
        cps = 10.0 if name == "intro" else (10.9 if pair == "pair-01" else 10.7)
        return {"name": name, "characters_per_second": cps}

    monkeypatch.setattr(builder, "narration_pace_profile", profile)

    records, summary = builder._prepare_narration_sections(sections)

    assert builder.NARRATION_GENERATION_PROTOCOL == "single_take_reference_restoration_v1"
    assert builder.NARRATION_PAIR_PREFLIGHT_MAX == 1.08
    assert policy.NARRATION["section_count"] == 7
    assert policy.NARRATION["section_gap_seconds"] == 0.0
    assert policy.NARRATION["last_to_first_pace_ratio_max"] == 1.10
    assert policy.TAILBITE == {"threshold_db": -35.0, "minimum": 0.08, "retained_gap": 0.06}
    assert [call["audio"].name for call in calls] == [
        "01_intro.mp3", "07_cta.mp3", "01_intro.mp3", "07_cta.mp3",
        "02_first.mp3", "03_second.mp3", "04_third.mp3", "05_fourth.mp3", "06_fifth.mp3",
    ]
    assert [(call["previous_text"], call["next_text"]) for call in calls[:4]] == [
        (None, "first"), ("fifth", None), (None, "first"), ("fifth", None),
    ]
    assert [(call["previous_text"], call["next_text"]) for call in calls[4:]] == [
        ("intro", "second"), ("first", "third"), ("second", "fourth"),
        ("third", "fifth"), ("fourth", "fixed cta"),
    ]
    assert summary["status"] == "pass"
    assert summary["selected_pair"] == "pair-02"
    assert summary["selected_pair_preflight_ratio"] == 1.07
    assert summary["pairs"][0]["pair_disposition"] == "discard_both"
    assert summary["pairs"][1]["pair_disposition"] == "selected"
    rejected = tmp_path / "narration_pair_preflight/pair-01"
    assert (rejected / "01_intro.mp3").is_file()
    assert (rejected / "07_cta.mp3").is_file()
    active = tmp_path / "narration_sections"
    assert (active / "01_intro.mp3").read_bytes().startswith(b"pair-02")
    assert (active / "07_cta.mp3").read_bytes().startswith(b"pair-02")
    assert len(records) == 7


def test_pair_preflight_never_generates_middle_sections_when_no_pair_passes(
    monkeypatch, tmp_path
):
    builder = load_builder()
    builder.ROOT = tmp_path
    builder.NARRATION_PAIR_MAX_ATTEMPTS = 2
    calls = []
    sections = ["intro", "first", "second", "third", "fourth", "fifth", "fixed cta"]
    monkeypatch.setattr(builder, "generate_minsoo_section", fake_section_writer(builder, calls))
    monkeypatch.setattr(
        builder,
        "narration_pace_profile",
        lambda _audio, _alignment, name: {
            "name": name,
            "characters_per_second": 10.0 if name == "intro" else 10.9,
        },
    )

    with pytest.raises(RuntimeError, match="1.08 preflight reserve"):
        builder._prepare_narration_sections(sections)

    assert [call["audio"].name for call in calls] == [
        "01_intro.mp3", "07_cta.mp3", "01_intro.mp3", "07_cta.mp3",
    ]
    assert not (tmp_path / "narration_sections").exists()
    summary = json.loads(
        (tmp_path / "narration_pair_preflight/summary.json").read_text(encoding="utf-8")
    )
    assert summary["status"] == "fail"
    assert all(pair["pair_disposition"] == "discard_both" for pair in summary["pairs"])


def test_section_request_carries_locked_voice_and_neighbor_context(monkeypatch, tmp_path):
    builder = load_builder()
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "audio_base64": base64.b64encode(b"offline-audio").decode(),
                "normalized_alignment": {
                    "characters": ["가"],
                    "character_start_times_seconds": [0.0],
                    "character_end_times_seconds": [0.1],
                },
            }).encode()

    def urlopen(request, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setattr(builder.shorts_video, "_elevenlabs_api_keys", lambda: ["offline-key"])
    monkeypatch.setattr(builder.urllib.request, "urlopen", urlopen)
    audio = tmp_path / "audio.mp3"
    alignment = tmp_path / "alignment.json"

    builder.generate_minsoo_section(
        "current", audio, alignment,
        previous_text="previous", next_text="next", seed=1234,
    )

    payload = json.loads(requests[0][0].data.decode())
    assert payload == {
        "text": "current",
        "model_id": policy.MINSOO_MODEL_ID,
        "voice_settings": policy.MINSOO_VOICE_SETTINGS,
        "seed": 1234,
        "previous_text": "previous",
        "next_text": "next",
    }
    evidence = json.loads(alignment.read_text(encoding="utf-8"))
    assert evidence["generation_protocol"] == builder.NARRATION_GENERATION_PROTOCOL
    assert evidence["previous_text_sha256"] == hashlib.sha256(b"previous").hexdigest()
    assert evidence["next_text_sha256"] == hashlib.sha256(b"next").hexdigest()


def test_exact_runtime_gate_requires_the_selected_pair_files(monkeypatch, tmp_path):
    builder = load_builder()
    monkeypatch.setattr(builder, "NARRATION_GENERATION_PROTOCOL", "paired_intro_cta_preflight_full_candidate_v0")
    builder.ROOT = tmp_path
    builder.SCRIPT = tmp_path / "07_script_final.txt"
    script = "도입 첫째 둘째 셋째 넷째 다섯째 CTA"
    builder.SCRIPT.write_text(script, encoding="utf-8")
    pair = tmp_path / "narration_pair_preflight/pair-01"
    active = tmp_path / "narration_sections"
    pair.mkdir(parents=True)
    active.mkdir()
    for filename in (
        "01_intro.mp3", "01_intro_alignment.json", "07_cta.mp3", "07_cta_alignment.json"
    ):
        (pair / filename).write_bytes(filename.encode())
        (active / filename).write_bytes(filename.encode())
    (tmp_path / "narration_pair_preflight/summary.json").write_text(json.dumps({
        "protocol": builder.NARRATION_GENERATION_PROTOCOL,
        "status": "pass",
        "preflight_maximum": 1.08,
        "exact_runtime_maximum": 1.10,
        "selected_pair": "pair-01",
        "selected_pair_preflight_ratio": 1.07,
        "pairs": [{
            "pair": "pair-01",
            "status": "pass",
            "pair_disposition": "selected",
            "last_to_first_ratio": 1.07,
        }],
    }), encoding="utf-8")
    characters = list("가나다라마바사")
    starts = [index + 0.1 for index in range(7)]
    ends = [index + 0.2 for index in range(7)]
    (tmp_path / "narration_alignment.json").write_text(json.dumps({
        "script_sha256": hashlib.sha256(script.encode()).hexdigest(),
        "generation_protocol": builder.NARRATION_GENERATION_PROTOCOL,
        "selected_pair": "pair-01",
        "selected_pair_preflight_ratio": 1.07,
        "sections": [
            {"name": name, "script": script.split()[index], "combined_offset_seconds": index, "duration_seconds": 0.9}
            for index, name in enumerate(builder.NARRATION_SECTION_NAMES)
        ],
        "alignment": {
            "characters": characters,
            "character_start_times_seconds": starts,
            "character_end_times_seconds": ends,
        },
    }), encoding="utf-8")
    (tmp_path / "captions.srt").write_text("offline captions", encoding="utf-8")
    captions = [{"text": token, "start": index, "end": index + len(token) / 10} for index, token in enumerate(script.split())]
    segment = builder.tailbite.Segment(start=0.0, end=7.0, output_start=0.0)

    gate = builder.build_runtime_gate([segment], captions)

    assert gate["status"] == "pass"
    assert gate["pace_uniformity"]["maximum"] == 1.10
    assert gate["recovery_protocol"]["paired_preflight_maximum"] == 1.08
    assert gate["recovery_protocol"]["selected_pair_files_match"] is True

    (active / "07_cta.mp3").write_bytes(b"not-the-selected-pair")
    with pytest.raises(RuntimeError, match="exact runtime gate failed"):
        builder.build_runtime_gate([segment], captions)
    failed = json.loads((tmp_path / "02_exact_runtime_gate.json").read_text(encoding="utf-8"))
    assert failed["status"] == "fail"
    assert failed["recovery_protocol"]["selected_pair_files_match"] is False
