import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import content_production_policy as policy
import notebooklm_shorts as shorts
import shorts_video
import youtube_cardnews_pipeline as pipeline


def test_notebooklm_is_pinned_to_minsoo_notebooks_and_aside_u0():
    assert policy.ASIDE_ACCOUNT == "u0"
    assert policy.validate_notebook_binding(
        "cafe", account="u0", title="민수대표님_카페글",
        notebook_id="c09a56d4-b87c-4f54-bfdb-93219326fbae",
    )
    assert policy.validate_notebook_binding(
        "shorts", account="u0", title="민수대표님_숏폼",
        notebook_id="ed70fc3b-474b-423a-9ca8-d19934703f27",
    )
    with pytest.raises(policy.ProductionPolicyError, match="금지"):
        policy.validate_notebook_binding(
            "cafe", account="u0", title="그지마케팅_카페글",
            notebook_id=policy.CAFE_NOTEBOOK["id"],
        )


def test_minsoo_voice_settings_and_v7_layout_are_machine_constants():
    assert policy.MINSOO_VOICE_SETTINGS == {
        "stability": 0.65,
        "similarity_boost": 0.9,
        "style": 0.0,
        "use_speaker_boost": True,
    }
    assert policy.HEADLINE["font_size"] == 90
    assert policy.PRESENTER == {
        "x": 325, "y": 1298, "width": 430, "height": 430, "shape": "circle"
    }
    assert policy.SOURCE_SCREEN["width"] == 1080
    assert policy.CARDNEWS["aspect_ratio"] == "1:1"


def test_subtitle_edge_punctuation_is_removed_but_internal_marks_are_preserved(tmp_path):
    assert policy.strip_subtitle_edge_punctuation("대박입니다.") == "대박입니다"
    assert policy.strip_subtitle_edge_punctuation("(저장하세요!)") == "저장하세요"
    assert policy.strip_subtitle_edge_punctuation("저장하세요~") == "저장하세요"
    assert policy.strip_subtitle_edge_punctuation("fal.ai") == "fal.ai"
    assert policy.strip_subtitle_edge_punctuation("2.6") == "2.6"

    good = tmp_path / "good.srt"
    good.write_text("1\n00:00:00,000 --> 00:00:01,000\nfal.ai\n\n2\n00:00:01,000 --> 00:00:02,000\n대박입니다\n", encoding="utf-8")
    assert policy.validate_subtitle_file(good) == {
        "caption_count": 2,
        "edge_punctuation_violations": 0,
    }

    bad = tmp_path / "bad.srt"
    bad.write_text("1\n00:00:00,000 --> 00:00:01,000\n대박입니다.\n", encoding="utf-8")
    with pytest.raises(policy.ProductionPolicyError, match="문장부호"):
        policy.validate_subtitle_file(bad)


def test_all_four_downloaded_minsoo_assets_have_locked_hashes():
    downloads = Path("/Users/apple/Downloads")
    for name in policy.MINSOO_PRESENTER_ASSETS:
        assert policy.validate_presenter_asset(downloads / name)["sha256"]


def test_schedule_allows_two_per_day_with_five_hour_gap():
    zone = timezone(timedelta(hours=9))
    slots = [
        datetime(2026, 8, 24, 11, tzinfo=zone),
        datetime(2026, 8, 24, 20, tzinfo=zone),
    ]
    assert policy.validate_schedule(slots) == slots
    with pytest.raises(policy.ProductionPolicyError, match="최소 5시간"):
        policy.validate_schedule([slots[0], slots[0] + timedelta(hours=4)])


def test_schedule_includes_saturday_and_sunday():
    zone = timezone(timedelta(hours=9))
    weekend_slots = [
        datetime(2026, 8, 29, 11, tzinfo=zone),
        datetime(2026, 8, 29, 20, tzinfo=zone),
        datetime(2026, 8, 30, 11, tzinfo=zone),
        datetime(2026, 8, 30, 20, tzinfo=zone),
    ]
    assert policy.SCHEDULE["include_weekends"] is True
    assert policy.validate_schedule(weekend_slots) == weekend_slots


def test_replacement_sequence_is_fail_closed():
    policy.validate_replacement_sequence([
        "new_provider_verified", "old_cancelled_or_hidden", "old_deleted", "crm_sent"
    ])
    with pytest.raises(policy.ProductionPolicyError, match="순서"):
        policy.validate_replacement_sequence([
            "old_deleted", "new_provider_verified", "old_cancelled_or_hidden", "crm_sent"
        ])


def test_approved_v7_reference_passes_upload_policy():
    video = Path(
        "outputs/20260822-shorts-correction-audit/rebaseline/"
        "remade-v7-reference-restored/GExjqEBXKN4/final.mp4"
    )
    assert policy.validate_shorts_bundle_for_upload(video)["video_sha256"]


def test_legacy_renderer_is_disabled():
    with pytest.raises(RuntimeError, match="프레임 슬라이드"):
        shorts_video.render_short_video("대본", [], "/tmp/never-created")


def test_cardnews_rejects_portrait_and_non_ten_slide_decks(tmp_path):
    with pytest.raises(RuntimeError, match="1080x1080"):
        pipeline.render_cardnews_pngs({"slides": [{}] * 10}, tmp_path, aspect="portrait")
    with pytest.raises(RuntimeError, match="정확히 10장"):
        pipeline.render_cardnews_pngs({"slides": [{}] * 9}, tmp_path, aspect="square")


def test_keeps_through_fifth_and_replaces_sixth_and_old_cta():
    original = """첫째, 하나

둘째, 둘

셋째, 셋

넷째, 넷

다섯째, 다섯

여섯째, 여섯

이 영상을 정리했습니다.
자료가 궁금하신 분들은 구독하세요."""
    result = shorts.finalize_script(original, 23)
    assert "다섯째, 다섯" in result
    assert "여섯째" not in result
    assert result.endswith(
        "23분 짜리 영상 내용을 모두 정리했습니다.\n\n"
        "이 자료 궁금하신 분들은 채널을 구독후 프로필 링크를 확인하세요."
    )


def test_numeric_sixth_is_cut():
    original = "1. 하나\n2. 둘\n3. 셋\n4. 넷\n5. 다섯\n6. 여섯"
    assert "6. 여섯" not in shorts.finalize_script(original, 10)


def test_everything_after_fifth_block_is_replaced_even_without_cta_keywords():
    original = """첫째, 하나

둘째, 둘

셋째, 셋

넷째, 넷

다섯째, 다섯의 설명입니다.
두 번째 설명입니다.

\"마지막 명언\"
무료 가이드를 보내드립니다."""
    result = shorts.finalize_script(original, 17)
    assert "두 번째 설명입니다." in result
    assert "마지막 명언" not in result
    assert "무료 가이드" not in result
    assert result.endswith(
        "17분 짜리 영상 내용을 모두 정리했습니다.\n\n"
        "이 자료 궁금하신 분들은 채널을 구독후 프로필 링크를 확인하세요."
    )


@pytest.mark.parametrize(
    ("seconds", "minutes"),
    [(60, 1), (89, 1), (90, 2), (1259, 21)],
)
def test_duration_minutes_rounds_to_nearest(seconds, minutes):
    assert shorts.duration_minutes(seconds) == minutes


@pytest.mark.parametrize(
    "line",
    [
        "이 남자 미쳤습니다.",
        "이 프로그램 대박입니다.",
        "여기, 이 남자는 영상 편집의 천재입니다.",
    ],
)
def test_existing_strong_hook_shapes_are_accepted(line):
    assert shorts.require_strong_hook(line + "\n다음 문장") == line


def test_generic_explanatory_hook_is_rejected():
    with pytest.raises(RuntimeError, match="강한 후킹"):
        shorts.require_strong_hook("결과가 비슷해 보인다면 바꿔야 할 게 있습니다.")


def test_extracts_three_ranked_two_line_head_copies():
    answer = """### 헤드카피라이팅
1. 클로드가 영상도 만든다고? / 디자인 AI 티 없애는법
2. 이거 그냥 쓰면 손해 / 클로드 디자인 바꾸는법
3. 5단계면 충분합니다 / AI 모션그래픽 개선법

### 스크립트
이 프로그램 대박입니다.
다음 문장입니다.
"""
    candidates = shorts.extract_head_copy_candidates(answer)
    assert candidates == [
        "클로드가 영상도 만든다고?\n디자인 AI 티 없애는법",
        "이거 그냥 쓰면 손해\n클로드 디자인 바꾸는법",
        "5단계면 충분합니다\nAI 모션그래픽 개선법",
    ]


def test_generic_noun_head_copy_is_rejected():
    with pytest.raises(RuntimeError, match="구어체"):
        shorts.validate_head_copy("클로드 디자인 / 모션그래픽 5가지")


def test_head_copy_must_connect_to_the_script_opening():
    script = """이 프로그램 대박입니다.
클로드로 디자인의 AI 티를 지우는 방법입니다.
모션그래픽 5가지를 저장하고 끝까지 보세요."""
    assert shorts.validate_head_copy_connection(
        "클로드가 디자인도 한다고? / AI 티 없애는법", script
    )
    with pytest.raises(RuntimeError, match="연결되지"):
        shorts.validate_head_copy_connection(
            "오늘 안 보면 손해 / 엑셀 수익화 하는법", script
        )


def test_head_copy_rejects_hashtags_and_long_lines():
    with pytest.raises(RuntimeError, match="해시태그"):
        shorts.validate_head_copy("이거 그냥 쓰면 손해 / #클로드 수익화")
    with pytest.raises(RuntimeError, match="18자"):
        shorts.validate_head_copy(
            "이것은 한 줄이 열여덟 글자를 확실하게 넘습니다 / AI 수익화"
        )


def test_shorts_title_removes_all_trailing_hashtags():
    assert shorts_video.normalize_shorts_title(
        "클로드 디자인 5단계 #AI #Shorts"
    ) == "클로드 디자인 5단계"
    assert shorts_video.normalize_shorts_title("클로드 디자인 5단계") == "클로드 디자인 5단계"
