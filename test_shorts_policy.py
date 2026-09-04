import json

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
    zone = ZoneInfo("Asia/Seoul")
    slots = [
        datetime(2026, 8, 24, 11, tzinfo=zone),
        datetime(2026, 8, 24, 20, tzinfo=zone),
    ]
    assert policy.validate_schedule(slots) == slots
    with pytest.raises(policy.ProductionPolicyError, match="최소 5시간"):
        policy.validate_schedule([slots[0], slots[0] + timedelta(hours=4)])


def test_schedule_includes_saturday_and_sunday():
    zone = ZoneInfo("Asia/Seoul")
    weekend_slots = [
        datetime(2026, 8, 29, 11, tzinfo=zone),
        datetime(2026, 8, 29, 20, tzinfo=zone),
        datetime(2026, 8, 30, 11, tzinfo=zone),
        datetime(2026, 8, 30, 20, tzinfo=zone),
    ]
    assert policy.SCHEDULE["include_weekends"] is True
    assert policy.validate_schedule(weekend_slots) == weekend_slots


def test_schedule_rejects_fixed_offset_and_naive_datetimes():
    fixed_offset = timezone(timedelta(hours=9))
    with pytest.raises(policy.ProductionPolicyError, match="Asia/Seoul"):
        policy.validate_schedule([datetime(2026, 9, 17, 11, tzinfo=fixed_offset)])
    with pytest.raises(policy.ProductionPolicyError, match="Asia/Seoul"):
        policy.validate_schedule([datetime(2026, 9, 17, 11)])


def test_plan_shorts_schedule_is_append_only_and_gap_safe():
    zone = ZoneInfo("Asia/Seoul")
    now = datetime(2026, 9, 5, 0, 30, tzinfo=zone)
    assert policy.plan_shorts_schedule([], now) == datetime(2026, 9, 5, 11, tzinfo=zone)
    assert policy.plan_shorts_schedule(
        [datetime(2026, 9, 16, 11, tzinfo=zone)], now
    ) == datetime(2026, 9, 16, 20, tzinfo=zone)
    assert policy.plan_shorts_schedule(
        [datetime(2026, 9, 16, 20, tzinfo=zone)], now
    ) == datetime(2026, 9, 17, 11, tzinfo=zone)
    assert policy.plan_shorts_schedule(
        [datetime(2026, 9, 16, 18, tzinfo=zone)], now
    ) == datetime(2026, 9, 17, 11, tzinfo=zone)


def test_replacement_sequence_is_fail_closed():
    policy.validate_replacement_sequence([
        "new_provider_verified", "old_cancelled_or_hidden", "old_deleted", "crm_sent"
    ])
    with pytest.raises(policy.ProductionPolicyError, match="순서"):
        policy.validate_replacement_sequence([
            "old_deleted", "new_provider_verified", "old_cancelled_or_hidden", "crm_sent"
        ])


def test_community_provider_text_uses_exact_endpoint_for_truncated_url():
    source_url = "https://www.youtube.com/watch?v=dCLW6IQt06M"
    expected = f"본문입니다.\n\n원본 영상: {source_url}"
    runs = [
        {"text": "본문입니다.\n\n원본 영상: "},
        {
            "text": "https://www.youtube.com/watch?v=dCLW6...",
            "navigationEndpoint": {"urlEndpoint": {"url": "/watch?v=dCLW6IQt06M"}},
        },
    ]

    evidence = policy.validate_community_provider_text(expected, source_url, runs)

    assert evidence["checks"] == {
        "body_exact": True,
        "source_url_endpoint_exact": True,
        "source_url_in_reconstructed_body": True,
    }
    assert evidence["rendered_text_was_truncated"] is True
    assert evidence["truncated_run_count"] == 1


def test_community_provider_text_rejects_missing_or_wrong_endpoint():
    source_url = "https://www.youtube.com/watch?v=dCLW6IQt06M"
    expected = f"원본 영상: {source_url}"
    with pytest.raises(policy.ProductionPolicyError, match="endpoint"):
        policy.validate_community_provider_text(
            expected,
            source_url,
            [{"text": "원본 영상: https://www.youtube.com/watch?v=dCLW6..."}],
        )
    with pytest.raises(policy.ProductionPolicyError, match="endpoint"):
        policy.validate_community_provider_text(
            expected,
            source_url,
            [{
                "text": "원본 영상: https://www.youtube.com/watch?v=dCLW6...",
                "navigationEndpoint": {"urlEndpoint": {"url": "/watch?v=WRONG"}},
            }],
        )


def test_self_contained_v7_bundle_passes_upload_policy(tmp_path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"self-contained-test-video")
    presenter_name, presenter_hash = next(iter(policy.MINSOO_PRESENTER_ASSETS.items()))
    render = {
        "output_width": policy.VIDEO["width"],
        "output_height": policy.VIDEO["height"],
        "fps": policy.VIDEO["fps"],
        "title": policy.HEADLINE,
        "subtitle": policy.SUBTITLE,
        "presenter": policy.PRESENTER,
        "screen": policy.SOURCE_SCREEN,
        "watermark": policy.WATERMARK,
        "render_provenance": {
            "source_and_presenter_audio_mapped": False,
            "source_audio_mapped": False,
            "presenter_audio_mapped": False,
            "presenter_input": presenter_name,
            "presenter_sha256": presenter_hash,
            "source_footage": "source.mp4",
            "source_footage_sha256": "source-sha256",
            "v7_reference_restoration": {
                "voice_settings": policy.MINSOO_VOICE_SETTINGS,
                "headline_font_size_1080": policy.HEADLINE["font_size"],
                "subtitle_rule": policy.SUBTITLE["rule"],
            },
        },
    }
    machine = {
        "status": "pass",
        "failures": [],
        "gates": {
            **{name: True for name in policy.REQUIRED_MACHINE_GATES},
            "source_audio_mapped": False,
            "presenter_audio_mapped": False,
        },
    }
    alignment = {
        "voice_id": policy.MINSOO_VOICE_ID,
        "model_id": policy.MINSOO_MODEL_ID,
        "settings": policy.MINSOO_VOICE_SETTINGS,
        "alignment": {
            "characters": ["가"],
            "character_start_times_seconds": [0.0],
        },
        "generation_mode": policy.NARRATION["generation_mode"],
        "section_count": policy.NARRATION["section_count"],
    }
    runtime = {
        "status": "pass",
        "script_alignment_hash_match": True,
        "caption_count_matches_script_tokens": True,
        "caption_tokens_are_whole": True,
        "pace_uniformity": {"status": "pass", "last_to_first_ratio": 1.0},
    }
    for name, payload in (
        ("render_config.json", render),
        ("machine_validation.json", machine),
        ("narration_alignment.json", alignment),
        ("02_exact_runtime_gate.json", runtime),
    ):
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "captions.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n검증완료\n",
        encoding="utf-8",
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


def _editorial_deck(heads):
    slides = [{"type": "cover", "f": {"title": "AI 도구 10개", "sub": "이번 주에 저장할 목록"}}]
    slides.extend(
        {"type": "content", "f": {"head": head, "desc": "원본에서 소개한 방법과 쓰임을 한 가지씩 설명합니다."}}
        for head in heads
    )
    slides.append({
        "type": "closing",
        "f": {"cta1": "댓글 AIMAX", "cta2": "관련 정보 받기"},
    })
    return {"slides": slides}


def test_cardnews_editorial_gate_accepts_source_first_story():
    deck = _editorial_deck([
        "플러그인을 고른다", "이미지를 만든다", "로컬 환경을 쓴다", "로그를 분석한다",
        "코딩 작업을 맡긴다", "영상을 자동화한다", "무료 모델을 비교한다", "권한을 확인한다",
    ])
    evidence = policy.validate_cardnews_editorial_deck(deck)
    assert evidence["useful_content_cards"] == 8
    assert evidence["warning_first_cards"] == 0


def test_cardnews_editorial_gate_rejects_disclaimer_report():
    deck = _editorial_deck([
        "제작자 주장입니다", "독립 검증이 필요합니다", "단정하면 안 됩니다", "보장하지 않습니다",
        "확인해야 합니다", "주의하세요", "방법을 살펴봅니다", "도구를 비교합니다",
    ])
    with pytest.raises(policy.ProductionPolicyError, match="면책·검증"):
        policy.validate_cardnews_editorial_deck(deck)


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


def test_shorts_uses_the_simple_notebooklm_request():
    assert shorts.SHORTS_PROMPT == "이 영상으로 숏폼 스크립트 만들어줘."


def test_cta_only_transform_preserves_notebooklm_body_exactly():
    original = """이 남자 미쳤습니다.

첫째, 원문 하나

둘째, 원문 둘

셋째, 원문 셋

넷째, 원문 넷

다섯째, 원문 다섯

자료가 궁금하신 분들은 기존 CTA를 확인하세요."""
    final = shorts.finalize_script(original, 12)
    report = shorts.cta_only_transform_report(original, final, 12)
    assert report["status"] == "cta_only"
    assert report["notebooklm_body_preserved_exactly"] is True
    assert report["content_rewrite_applied"] is False
    assert report["body_sha256_before"] == report["body_sha256_after"]


def test_cta_only_transform_rejects_any_body_rewrite():
    original = "첫째, 원문\n둘째, 둘\n셋째, 셋\n넷째, 넷\n다섯째, 다섯"
    rewritten = shorts.finalize_script(original, 5).replace("첫째, 원문", "첫째, 재작성")
    with pytest.raises(RuntimeError, match="CTA 교체 외에 변경"):
        shorts.cta_only_transform_report(original, rewritten, 5)


def test_fetch_keeps_notebooklm_body_and_only_replaces_cta(monkeypatch):
    answer = """헤드카피라이팅
1. 클로드가 다 한다고? / 자동화 핵심 5가지
2. 반복 업무 아직 해요? / 클로드로 줄이는 법
3. 이 기능 대박입니다 / 클로드 자동화 공개

스크립트
이 프로그램 대박입니다.
클로드가 반복 업무를 처리하는 흐름입니다.
다섯 가지 방법, 저장하고 끝까지 보세요!

첫째, 원문 하나

둘째, 원문 둘

셋째, 원문 셋

넷째, 원문 넷

다섯째, 원문 다섯

이 자료가 궁금하신 분들은 채널을 구독하세요."""
    monkeypatch.setattr(shorts, "load_shorts_config", lambda: ({"prompt": shorts.SHORTS_PROMPT}, ""))
    monkeypatch.setattr(shorts.nlm, "fetch_manuscript", lambda url, cfg, log=print: answer)
    monkeypatch.setattr(shorts, "get_video_duration", lambda url: 12 * 60)

    final, _format, minutes, raw, transform, _head_copies = shorts.fetch(
        "https://www.youtube.com/watch?v=example"
    )

    assert raw == answer
    assert minutes == 12
    assert final.startswith("이 프로그램 대박입니다.\n클로드가 반복 업무를 처리하는 흐름입니다.")
    assert "채널을 구독하세요" not in final
    assert final.endswith(shorts.fixed_cta(12))
    assert transform["status"] == "cta_only"
    assert transform["body_sha256_before"] == transform["body_sha256_after"]


def test_numeric_sixth_is_cut():
    original = "1. 하나\n2. 둘\n3. 셋\n4. 넷\n5. 다섯\n6. 여섯"
    assert "6. 여섯" not in shorts.finalize_script(original, 10)


def test_fifth_item_paragraphs_are_preserved_until_an_explicit_cta():
    original = """첫째, 하나

둘째, 둘

셋째, 셋

넷째, 넷

다섯째, 다섯의 설명입니다.
두 번째 설명입니다.

추가 설명도 본문입니다.

댓글에 자료 남겨주세요. 무료 가이드를 보내드립니다."""
    result = shorts.finalize_script(original, 17)
    assert "두 번째 설명입니다." in result
    assert "추가 설명도 본문입니다." in result
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


def test_extracts_plain_notebooklm_headings_without_markdown_hashes():
    answer = """헤드카피라이팅
1. 클로드가 영상도 만든다고? / 디자인 AI 티 없애는법
2. 이거 그냥 쓰면 손해 / 클로드 디자인 바꾸는법
3. 5단계면 충분합니다 / AI 모션그래픽 개선법

스크립트
이 프로그램 대박입니다.
다음 문장입니다.
"""
    assert len(shorts.extract_head_copy_candidates(answer)) == 3
    script, _ = shorts.extract_script(answer)
    assert script.startswith("이 프로그램 대박입니다.")


def test_extracts_three_unnumbered_notebooklm_head_copies():
    answer = """헤드카피라이팅
고딩이 월 2만 불 벌어?! / 클로드로 24시간 자동 영업
매달 제안서 쓰다 밤새워?! / 클로드로 5분 만에 완성함
에이아이 매번 새로 가르쳐?! / 옵시디언으로 뇌 이식하기
스크립트
이 남자 미쳤습니다.
다음 문장입니다.
"""
    candidates = shorts.extract_head_copy_candidates(answer)
    assert len(candidates) == 3


def test_plural_person_hook_and_same_line_body_are_accepted():
    assert shorts.require_strong_hook("이 남자들 미쳤습니다.\n다음 문장입니다.")
    script = "이 남자들 미쳤습니다. 매달 나가는 비용을 줄인 방법입니다."
    assert shorts.require_strong_hook(script) == "이 남자들 미쳤습니다."


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
