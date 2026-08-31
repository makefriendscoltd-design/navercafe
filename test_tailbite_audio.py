import json

import pytest

from build_tailbite_audio import Segment, build_captions


def _write_alignment(path, word_durations):
    characters = []
    starts = []
    ends = []
    for index, (start, end) in enumerate(word_durations):
        if index:
            characters.append(" ")
            starts.append(start)
            ends.append(start)
        characters.append("x")
        starts.append(start)
        ends.append(end)
    path.write_text(
        json.dumps(
            {
                "alignment": {
                    "characters": characters,
                    "character_start_times_seconds": starts,
                    "character_end_times_seconds": ends,
                }
            }
        ),
        encoding="utf-8",
    )


def test_captions_do_not_merge_neighboring_eojeol(tmp_path):
    script = tmp_path / "script.txt"
    alignment = tmp_path / "alignment.json"
    script.write_text("이 프로그램 대박입니다.", encoding="utf-8")
    _write_alignment(alignment, [(0.0, 0.2), (0.2, 0.8), (0.8, 1.4)])

    captions = build_captions(
        script,
        alignment,
        [Segment(start=0.0, end=1.4, output_start=0.0)],
    )

    assert [caption["text"] for caption in captions] == ["이", "프로그램", "대박입니다"]


def test_caption_keeps_long_eojeol_whole(tmp_path):
    script = tmp_path / "script.txt"
    alignment = tmp_path / "alignment.json"
    script.write_text("애니메이션입니다.", encoding="utf-8")
    _write_alignment(alignment, [(0.0, 1.6)])

    captions = build_captions(
        script,
        alignment,
        [Segment(start=0.0, end=1.6, output_start=0.0)],
    )

    assert [caption["text"] for caption in captions] == ["애니메이션입니다"]


def test_caption_never_splits_english_product_names(tmp_path):
    script = tmp_path / "script.txt"
    alignment = tmp_path / "alignment.json"
    script.write_text("Gmail Instantly Clay Microsoft365", encoding="utf-8")
    _write_alignment(alignment, [(0.0, 0.4), (0.4, 1.0), (1.0, 1.4), (1.4, 2.1)])

    captions = build_captions(
        script,
        alignment,
        [Segment(start=0.0, end=2.1, output_start=0.0)],
    )

    assert [caption["text"] for caption in captions] == [
        "Gmail", "Instantly", "Clay", "Microsoft365"
    ]


def test_minsoo_voice_is_the_only_renderer_voice():
    import shorts_video

    assert shorts_video.MINSOO_VOICE_ID == "34bevfaPHev7LXnjGAlA"
    source = open(shorts_video.__file__, encoding="utf-8").read()
    assert 'DEFAULT_VOICE = "Yuna"' not in source
    assert '"say"' not in source


def test_missing_elevenlabs_key_fails_instead_of_using_fallback(tmp_path, monkeypatch):
    import shorts_video

    monkeypatch.setattr(shorts_video, "_elevenlabs_api_keys", lambda: [])
    with pytest.raises(RuntimeError, match="다른 음성으로 대체하지 않고"):
        shorts_video._generate_minsoo_take(
            "이 프로그램 대박입니다.",
            tmp_path / "voice.mp3",
            tmp_path / "alignment.json",
        )


def test_upload_voice_gate_rejects_missing_or_wrong_voice(tmp_path):
    import shorts_video

    video = tmp_path / "short.mp4"
    video.write_bytes(b"placeholder")
    with pytest.raises(RuntimeError, match="증거 파일"):
        shorts_video.validate_minsoo_voice_artifact(video)

    alignment = tmp_path / "09_shorts_minsoo_alignment.json"
    alignment.write_text(
        json.dumps(
            {
                "voice_id": "wrong-voice",
                "model_id": shorts_video.MINSOO_MODEL_ID,
                "alignment": {
                    "characters": ["x"],
                    "character_start_times_seconds": [0.0],
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="일치하지 않아"):
        shorts_video.validate_minsoo_voice_artifact(video)


def test_upload_voice_gate_accepts_minsoo_alignment(tmp_path):
    import shorts_video

    video = tmp_path / "short.mp4"
    video.write_bytes(b"placeholder")
    alignment = tmp_path / "09_shorts_minsoo_alignment.json"
    alignment.write_text(
        json.dumps(
            {
                "voice_id": shorts_video.MINSOO_VOICE_ID,
                "model_id": shorts_video.MINSOO_MODEL_ID,
                "settings": shorts_video.MINSOO_VOICE_SETTINGS,
                "alignment": {
                    "characters": ["x"],
                    "character_start_times_seconds": [0.0],
                },
            }
        ),
        encoding="utf-8",
    )

    result = shorts_video.validate_minsoo_voice_artifact(video)

    assert result["voice_id"] == shorts_video.MINSOO_VOICE_ID
