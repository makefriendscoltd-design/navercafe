import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import content_production_policy as policy
import notebooklm_shorts as shorts
import shorts_video
import youtube_cardnews_pipeline as pipeline


FIXTURE_ROOT = Path(__file__).resolve().parent / "test_fixtures" / "notebooklm_shorts"


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
    original = """이 프로그램 대박입니다.

첫째, 하나

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
    assert shorts.SHORTS_PROMPT == policy.SHORTS_NOTEBOOK_PROMPT


def test_shorts_notebook_instruction_v14_is_hash_pinned_and_fail_closed():
    assert policy.SHORTS_NOTEBOOK_INSTRUCTION_VERSION == "v14.0"
    assert policy.notebook_instruction_sha256(policy.SHORTS_NOTEBOOK_INSTRUCTION) == (
        "503d5c7eb8564e5dd517154b876ecb8ad654f8092f3bf869f3f6b493f7a02f12"
    )
    assert policy.HEADLINE_SAFE_PROXY_CHAR_LIMIT == 13
    assert all(
        marker in policy.SHORTS_NOTEBOOK_INSTRUCTION
        for marker in policy.SHORTS_NOTEBOOK_REQUIRED_MARKERS
    )
    assert policy.validate_shorts_notebook_instruction(
        policy.SHORTS_NOTEBOOK_INSTRUCTION,
        goal="맞춤",
        response_length="길게",
    )["sha256"] == policy.SHORTS_NOTEBOOK_INSTRUCTION_SHA256
    with pytest.raises(policy.ProductionPolicyError, match="맞춤 지침"):
        policy.validate_shorts_notebook_instruction(
            policy.SHORTS_NOTEBOOK_INSTRUCTION + "\n임의 변경",
            goal="맞춤",
            response_length="길게",
        )
    with pytest.raises(policy.ProductionPolicyError, match="응답 길이"):
        policy.validate_shorts_notebook_instruction(
            policy.SHORTS_NOTEBOOK_INSTRUCTION,
            goal="맞춤",
            response_length="짧게",
        )


def test_captured_v12_dcl_response_is_an_exact_rejection_fixture():
    answer = (FIXTURE_ROOT / "v12_bad_dcl.md").read_text(encoding="utf-8").strip()
    assert hashlib.sha256(answer.encode("utf-8")).hexdigest() == (
        "1888dfb0c1467129a5d635c89ad1d8ac65688ddc953e116fef7d775fbc942a13"
    )

    with pytest.raises(RuntimeError, match="90px 안전폭 920px"):
        shorts.extract_head_copy_candidates(answer)

    script, _ = shorts.extract_script(answer)
    assert set(policy.find_forbidden_shorts_claims(script)) == {
        "all_or_any_source",
        "guaranteed_two_clicks",
        "free_or_unlimited",
        "fixed_generation_time",
        "automatic_cross_platform_distribution",
        "repurpose_boundary_missing",
        "repurpose_or_platform_distribution_extra",
    }
    with pytest.raises(policy.ProductionPolicyError, match="금지 주장"):
        policy.validate_shorts_verbatim_claims(script)


def test_v13_compliant_fixture_passes_pixel_claim_and_verbatim_cta_gates():
    answer = (FIXTURE_ROOT / "v13_compliant.md").read_text(encoding="utf-8").strip()
    head_copies = shorts.extract_head_copy_candidates(answer)
    assert len(head_copies) == 3
    assert all(
        max(policy.validate_headline_pixel_width(shorts.head_copy_lines(value))["line_widths_px"])
        <= policy.HEADLINE_SAFE_WIDTH_PX
        for value in head_copies
    )

    script, _ = shorts.extract_script(answer)
    assert policy.validate_shorts_verbatim_claims(script)["status"] == "pass"
    for ordinal in ("첫째", "둘째", "셋째", "넷째", "다섯째"):
        assert f"\n{ordinal}," in script
    final = shorts.finalize_script(script, 12)
    report = shorts.cta_only_transform_report(script, final, 12)
    assert report["notebooklm_body_preserved_exactly"] is True
    assert report["content_rewrite_applied"] is False
    assert report["body_sha256_before"] == report["body_sha256_after"]


def test_captured_v13_dcl_provider_response_is_preserved_and_rejected():
    answer = (FIXTURE_ROOT / "v13_failed_dcl_provider.md").read_text(encoding="utf-8")
    assert hashlib.sha256(answer.encode("utf-8")).hexdigest() == (
        "50357b323073ea24148d69d270c05e0ddaa7dd31dd13c100bd518547668c376d"
    )
    script, _ = shorts.extract_script(answer)
    hits = policy.find_forbidden_shorts_claims(script)
    assert "automatic_cross_platform_distribution" in hits
    assert "repurpose_boundary_missing" in hits
    assert "repurpose_or_platform_distribution_extra" in hits
    assert "영상 하나를 올리는 즉시 모든 채널에 자동으로 배포됩니다." in script
    with pytest.raises(policy.ProductionPolicyError, match="금지 주장"):
        policy.validate_shorts_verbatim_claims(script)


def test_forbidden_claim_gate_allows_attribution_and_conditional_caveats():
    qualified = """제작자는 모든 소스를 받는다고 말했지만 공식 지원 범위는 확인해야 합니다.
화자는 2클릭만으로 완성된다고 시연했지만 결과는 보장되지 않습니다.
원본은 무료라고 소개했지만 현재 요금은 확인이 필요합니다.
원본 화자는 5분에서 10분이면 생성된다고 말했지만 고정 시간은 아닙니다."""
    assert policy.find_forbidden_shorts_claims(qualified) == {}
    assert policy.validate_shorts_verbatim_claims(qualified)["status"] == "pass"


def test_repurpose_boundary_clauses_pass_only_when_both_are_exact():
    compliant = """Repurpose는 NotebookLM과 별개의 외부 워크플로우입니다.
별도 연결 설정과 각 플랫폼 공급자 지원이 확인된 채널에만 배포할 수 있습니다."""
    assert policy.find_forbidden_shorts_claims(compliant) == {}
    assert policy.validate_shorts_verbatim_claims(compliant)["status"] == "pass"


@pytest.mark.parametrize("duplicate_index", [0, 1, 2])
def test_repurpose_boundary_sentences_must_each_appear_exactly_once(duplicate_index):
    first, second = policy.SHORTS_REPURPOSE_BOUNDARY_SENTENCES
    extras = {0: first, 1: second, 2: first + "\n" + second}[duplicate_index]
    value = first + "\n" + second + "\n" + extras
    hits = policy.find_forbidden_shorts_claims(value)
    assert "repurpose_boundary_missing" not in hits
    assert "repurpose_boundary_cardinality" in hits
    with pytest.raises(policy.ProductionPolicyError, match="금지 주장"):
        policy.validate_shorts_verbatim_claims(value)


def test_v14_instruction_requires_source_ordered_points_without_global_overfit():
    instruction = policy.SHORTS_NOTEBOOK_INSTRUCTION
    assert "첫째부터 다섯째의 제목과 핵심 행동" in instruction
    assert "원본에서 확인한 다섯 지점을 실제 순서대로 각각 이어받는다" in instruction
    assert "일반적인 이름으로 바꾸거나 서로 다른 항목으로 대체하지 않는다" in instruction
    assert "원본과 일대일로 대응할 수 없으면 스크립트를 출력하지 않는다" in instruction
    assert "dCLW6IQt06M" not in instruction


@pytest.mark.parametrize(
    "value",
    [
        "Repurpose는 NotebookLM과 별개의 외부 워크플로우입니다.",
        "별도 연결 설정 후 리퍼퍼스로 지원 채널에 배포할 수 있습니다.",
    ],
)
def test_repurpose_boundary_requires_both_exact_clauses(value):
    assert "repurpose_boundary_missing" in policy.find_forbidden_shorts_claims(value)
    with pytest.raises(policy.ProductionPolicyError, match="금지 주장"):
        policy.validate_shorts_verbatim_claims(value)


@pytest.mark.parametrize(
    "extra",
    [
        "Repurpose를 한 번 연결하면 지원 채널에 자동 배포됩니다.",
        "제작자는 Repurpose로 인스타와 틱톡에 자동 배포했다고 시연했습니다.",
        "Repurpose에 한 번의 업로드로 지원 채널에 자동 배포됩니다.",
    ],
)
def test_repurpose_boundary_does_not_waive_unsupported_distribution_extras(extra):
    value = """Repurpose는 NotebookLM과 별개의 외부 워크플로우입니다.
별도 연결 설정과 각 플랫폼 공급자 지원이 확인된 채널에만 배포할 수 있습니다.
""" + extra
    hits = policy.find_forbidden_shorts_claims(value)
    assert "repurpose_boundary_missing" not in hits
    assert "repurpose_or_platform_distribution_extra" in hits
    with pytest.raises(policy.ProductionPolicyError, match="금지 주장"):
        policy.validate_shorts_verbatim_claims(value)


@pytest.mark.parametrize(
    "extra",
    [
        "Repurpose로 지원 채널에 자동 배포되지만 예약 기능이 없습니다.",
        "Repurpose로 지원 채널에 자동 배포되며 편집 기능이 없습니다.",
        "Repurpose로 지원 채널에 업로드되지만 분석 기능은 없습니다.",
        "Repurpose로 지원 채널에 자동 배포되지만 다운로드는 하지 못합니다.",
    ],
)
def test_unrelated_trailing_negation_does_not_waive_repurpose_distribution(extra):
    value = """Repurpose는 NotebookLM과 별개의 외부 워크플로우입니다.
별도 연결 설정과 각 플랫폼 공급자 지원이 확인된 채널에만 배포할 수 있습니다.
""" + extra
    hits = policy.find_forbidden_shorts_claims(value)
    assert "repurpose_or_platform_distribution_extra" in hits
    with pytest.raises(policy.ProductionPolicyError, match="금지 주장"):
        policy.validate_shorts_verbatim_claims(value)


@pytest.mark.parametrize(
    "extra",
    [
        "Repurpose로 자동 배포되지만 업로드하지 못합니다.",
        "Repurpose로 지원 채널에 배포되지만 자동 업로드 기능이 없습니다.",
        "Repurpose로 자동 배포되지만 다른 채널에는 게시하지 못합니다.",
    ],
)
def test_a_negated_second_predicate_does_not_waive_positive_distribution(extra):
    value = """Repurpose는 NotebookLM과 별개의 외부 워크플로우입니다.
별도 연결 설정과 각 플랫폼 공급자 지원이 확인된 채널에만 배포할 수 있습니다.
""" + extra
    hits = policy.find_forbidden_shorts_claims(value)
    assert "repurpose_or_platform_distribution_extra" in hits
    with pytest.raises(policy.ProductionPolicyError, match="금지 주장"):
        policy.validate_shorts_verbatim_claims(value)


@pytest.mark.parametrize(
    "extra",
    [
        "Repurpose로 자동 배포할 수 없지는 않습니다.",
        "Repurpose로 자동 배포할 수 없는 것은 아닙니다.",
        "Repurpose로 업로드하지 못하는 것은 아닙니다.",
        "Repurpose에는 자동 업로드 기능이 없는 것은 아닙니다.",
        "Repurpose로 자동 배포를 보장하지 않는 것은 아닙니다.",
        "Repurpose로 게시된다고 보장되지 않는 것은 아닙니다.",
    ],
)
def test_double_negative_reversal_is_not_accepted_as_direct_negation(extra):
    value = """Repurpose는 NotebookLM과 별개의 외부 워크플로우입니다.
별도 연결 설정과 각 플랫폼 공급자 지원이 확인된 채널에만 배포할 수 있습니다.
""" + extra
    hits = policy.find_forbidden_shorts_claims(value)
    assert "repurpose_or_platform_distribution_extra" in hits
    with pytest.raises(policy.ProductionPolicyError, match="금지 주장"):
        policy.validate_shorts_verbatim_claims(value)


@pytest.mark.parametrize(
    "extra",
    [
        "원본은 Repurpose라는 별도 외부 도구를 소개합니다.",
        "Repurpose가 자동 배포를 보장하지 않습니다.",
        "Repurpose로 게시된다고 보장되지 않습니다.",
        "Repurpose로 자동 배포할 수 없습니다.",
        "Repurpose로 업로드하지 못합니다.",
        "Repurpose에는 자동 업로드 기능이 없습니다.",
        "Repurpose에서는 다운로드하지 못합니다.",
        "Repurpose로 배포는 하지 못합니다.",
        "Repurpose로 배포하지는 못합니다.",
        "Repurpose로 배포할 수는 없습니다.",
        "Repurpose로 배포가 되지 않습니다.",
        "Repurpose에는 업로드 기능이 전혀 없습니다.",
        "Repurpose로 배포가 안 됩니다.",
    ],
)
def test_repurpose_boundary_rejects_every_additional_repurpose_sentence(extra):
    value = """Repurpose는 NotebookLM과 별개의 외부 워크플로우입니다.
별도 연결 설정과 각 플랫폼 공급자 지원이 확인된 채널에만 배포할 수 있습니다.
""" + extra
    assert "repurpose_or_platform_distribution_extra" in policy.find_forbidden_shorts_claims(value)
    with pytest.raises(policy.ProductionPolicyError, match="금지 주장"):
        policy.validate_shorts_verbatim_claims(value)


@pytest.mark.parametrize(
    "extra",
    [
        "이 도구로 지원 채널에 자동 배포됩니다.",
        "이 앱은 인스타와 틱톡에 게시하지 못합니다.",
        "해당 서비스로 모든 채널에 업로드된다는 뜻은 아닙니다.",
    ],
)
def test_repurpose_context_rejects_pronoun_distribution_extras(extra):
    value = """Repurpose는 NotebookLM과 별개의 외부 워크플로우입니다.
별도 연결 설정과 각 플랫폼 공급자 지원이 확인된 채널에만 배포할 수 있습니다.
""" + extra
    assert "repurpose_or_platform_distribution_extra" in policy.find_forbidden_shorts_claims(value)


@pytest.mark.parametrize(
    "value",
    [
        "인스타 게시가 가능합니다.",
        "인스타용 콘텐츠를 업로드합니다.",
        "콘텐츠를 인스타에 올립니다.",
        "틱톡으로 내보낼 수 있습니다.",
        "SNS에 발행할 수 있습니다.",
    ],
)
def test_platform_only_distribution_vocabulary_requires_exact_boundary(value):
    hits = policy.find_forbidden_shorts_claims(value)
    assert "repurpose_boundary_missing" in hits
    assert "repurpose_or_platform_distribution_extra" in hits


@pytest.mark.parametrize(
    "extra",
    [
        "그 도구로 배포됩니다.",
        "그 앱에서 업로드합니다.",
        "이것으로 게시하지 않습니다.",
        "이 워크플로우로 유포됩니다.",
    ],
)
def test_boundary_context_tracks_broader_demonstrative_actors(extra):
    value = """Repurpose는 NotebookLM과 별개의 외부 워크플로우입니다.
별도 연결 설정과 각 플랫폼 공급자 지원이 확인된 채널에만 배포할 수 있습니다.
""" + extra
    assert "repurpose_or_platform_distribution_extra" in policy.find_forbidden_shorts_claims(value)


@pytest.mark.parametrize(
    "value",
    [
        "원본 영상을 NotebookLM에 업로드합니다.",
        "유튜브 영상을 소스로 업로드합니다.",
        "PDF 파일과 원본 문서를 업로드합니다.",
    ],
)
def test_ordinary_source_upload_without_distribution_context_is_allowed(value):
    assert policy.find_forbidden_shorts_claims(value) == {}


@pytest.mark.parametrize(
    "value",
    [
        "유튜브 쇼츠 영상을 NotebookLM에 업로드합니다.",
        "틱톡 영상을 소스로 NotebookLM에 업로드합니다.",
        "NotebookLM에 인스타 영상을 업로드합니다.",
        "인스타 게시물을 NotebookLM에 업로드합니다.",
        "NotebookLM에 인스타 게시물을 업로드합니다.",
    ],
)
def test_platform_named_source_ingestion_to_notebooklm_is_allowed(value):
    assert policy.find_forbidden_shorts_claims(value) == {}


def test_notebooklm_named_video_uploaded_to_platform_is_still_distribution():
    value = "NotebookLM 영상을 틱톡에 업로드합니다."
    hits = policy.find_forbidden_shorts_claims(value)
    assert "repurpose_boundary_missing" in hits
    assert "repurpose_or_platform_distribution_extra" in hits


def test_notebooklm_ingestion_exception_does_not_hide_compound_distribution():
    value = "NotebookLM에 인스타 영상을 업로드하고 틱톡에 게시합니다."
    hits = policy.find_forbidden_shorts_claims(value)
    assert "repurpose_boundary_missing" in hits
    assert "repurpose_or_platform_distribution_extra" in hits


def test_notebooklm_ingestion_exception_rejects_external_euro_destination():
    value = "NotebookLM에 인스타 영상을 틱톡으로 업로드합니다."
    hits = policy.find_forbidden_shorts_claims(value)
    assert "repurpose_boundary_missing" in hits
    assert "repurpose_or_platform_distribution_extra" in hits


@pytest.mark.parametrize(
    "value",
    [
        "제작자는 화면 구성을 소개했지만 이 도구는 어떤 자료든 처리합니다.",
        "이 도구는 무료지만 지원 범위는 확인이 필요합니다.",
        "모든 SNS 채널에 자동으로 배포됩니다.",
        "모든 채널에 배포됩니다.",
        "영상 하나를 올리는 즉시 지원 채널에 게시됩니다.",
        "영상 하나를 올리는 즉시 모든 채널에 자동으로 배포됩니다.",
        "클릭 두 번이면 영상이 완성됩니다.",
        "버튼을 두 번 누르면 영상이 완성됩니다.",
        "10분 안에 영상이 완성됩니다.",
        "모든 종류의 파일을 처리합니다.",
    ],
)
def test_forbidden_claim_gate_cannot_be_bypassed_by_unrelated_or_missing_forms(value):
    assert policy.find_forbidden_shorts_claims(value)
    with pytest.raises(policy.ProductionPolicyError, match="금지 주장"):
        policy.validate_shorts_verbatim_claims(value)


@pytest.mark.parametrize(
    "value",
    [
        "이 도구는 무료가 아닙니다.",
        "모든 소스를 지원하는 것은 아닙니다.",
        "2클릭만으로 완성된다고 보장하지 않습니다.",
        "5분에서 10분이면 완성된다는 뜻은 아닙니다.",
    ],
)
def test_forbidden_claim_gate_allows_directly_bound_negation(value):
    assert policy.find_forbidden_shorts_claims(value) == {}


def test_platform_distribution_negation_still_requires_exact_boundary_only():
    value = "NotebookLM에서 인스타와 틱톡으로 자동 배포되는 자체 기능은 아닙니다."
    hits = policy.find_forbidden_shorts_claims(value)
    assert "repurpose_boundary_missing" in hits
    assert "repurpose_or_platform_distribution_extra" in hits


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
    original = "이 프로그램 대박입니다.\n첫째, 원문\n둘째, 둘\n셋째, 셋\n넷째, 넷\n다섯째, 다섯"
    rewritten = shorts.finalize_script(original, 5).replace("첫째, 원문", "첫째, 재작성")
    with pytest.raises(RuntimeError, match="CTA 교체 외에 변경"):
        shorts.cta_only_transform_report(original, rewritten, 5)


@pytest.mark.parametrize(
    "script",
    [
        "이 프로그램 대박입니다.\n첫째, 1\n둘째, 2\n셋째, 3\n넷째, 4",
        "첫째, 1\n둘째, 2\n셋째, 3\n넷째, 4\n다섯째, 5",
        "이 프로그램 대박입니다.\n첫째, 1\n둘째, 2\n셋째, 3\n넷째, 4\n다섯째, 5\n다섯째, 중복",
        "이 프로그램 대박입니다.\n첫째, 1\n둘째, 2\n셋째, 3\n넷째, 4\n다섯째, 5\n[CTA] 임의 문구",
        "이 프로그램 대박입니다.\n[00:12]\n첫째, 1\n둘째, 2\n셋째, 3\n넷째, 4\n다섯째, 5",
    ],
)
def test_script_structure_fails_closed_without_exact_intro_and_first_through_fifth(script):
    with pytest.raises(RuntimeError):
        shorts.validate_script_structure(script)


def test_cta_report_seals_provider_cleanup_parser_and_adopted_hash_stages():
    provider = """### 스크립트
이 프로그램 대박입니다.[1]

첫째, 원문 하나 [2]

둘째, 원문 둘

셋째, 원문 셋

넷째, 원문 넷

다섯째, 원문 다섯"""
    stripped = shorts.nlm._strip_citations(provider)
    script, _ = shorts.extract_script(stripped)
    final = shorts.finalize_script(script, 12)
    report = shorts.cta_only_transform_report(
        script,
        final,
        12,
        provider_answer=provider,
        citation_stripped_answer=stripped,
    )
    assert report["provider_answer_sha256"] != report["citation_stripped_answer_sha256"]
    assert report["citation_cleanup_changed_provider_answer"] is True
    assert report["adopted_body_sha256"] == report["final_adopted_body_sha256"]
    assert report["notebooklm_body_preserved_exactly"] is True


def test_same_instruction_attempt_is_blocked_before_second_provider_call(monkeypatch, tmp_path):
    answer = (FIXTURE_ROOT / "v13_compliant.md").read_text(encoding="utf-8")
    provider = mock.Mock(return_value=answer)
    monkeypatch.setattr(shorts.nlm, "fetch_manuscript", provider)
    monkeypatch.setattr(shorts, "get_video_duration", lambda _url: 12 * 60)
    ledger = tmp_path / "attempt-ledger.json"
    url = "https://www.youtube.com/watch?v=KJWaxYpcXoo"
    shorts.fetch(url, attempt_ledger_path=ledger)
    with pytest.raises(policy.ProductionPolicyError, match="재추출"):
        shorts.fetch(url, attempt_ledger_path=ledger)
    assert provider.call_count == 1


def test_different_instruction_hash_does_not_block_retry_contract():
    prior = [{
        "source_key": "KJWaxYpcXoo",
        "instruction_version": "v12.0",
        "instruction_sha256": "old-hash",
        "attempt_status": "substantive_failed",
        "substantive_failure": True,
    }]
    assert policy.validate_shorts_notebook_retry("KJWaxYpcXoo", prior)["status"] == "pass"


def test_attempt_reservation_is_atomic_for_same_source_and_instruction(tmp_path):
    ledger = tmp_path / "attempt-ledger.json"

    def reserve(attempt_id):
        try:
            shorts.reserve_shorts_attempt(
                ledger,
                "KJWaxYpcXoo",
                attempt_id=attempt_id,
            )
            return "reserved"
        except policy.ProductionPolicyError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ("one", "two")))
    assert sorted(results) == ["blocked", "reserved"]
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    assert len(payload["attempts"]) == 1


def test_existing_provider_evidence_blocks_same_instruction_before_reservation(tmp_path):
    source_root = tmp_path / "outputs" / "KJWaxYpcXoo-20260905"
    evidence = source_root / "shorts" / "canary" / "notebooklm-provider-evidence.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(json.dumps({
        "status": "ok",
        "sourceUrl": "https://www.youtube.com/watch?v=KJWaxYpcXoo",
        "instructionEvidence": {
            "version": policy.SHORTS_NOTEBOOK_INSTRUCTION_VERSION,
            "sha256": policy.SHORTS_NOTEBOOK_INSTRUCTION_SHA256,
        },
    }), encoding="utf-8")
    ledger = source_root / "shorts" / "notebooklm-attempt-ledger.json"
    records = shorts.load_shorts_attempt_evidence("KJWaxYpcXoo", ledger, source_root)
    with pytest.raises(policy.ProductionPolicyError, match="재추출"):
        policy.validate_shorts_notebook_retry("KJWaxYpcXoo", records)


def test_fetch_keeps_notebooklm_body_and_only_replaces_cta(monkeypatch, tmp_path):
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
        "https://www.youtube.com/watch?v=KJWaxYpcXoo",
        attempt_ledger_path=tmp_path / "attempt-ledger.json",
    )

    assert raw == answer
    assert minutes == 12
    assert final.startswith("이 프로그램 대박입니다.\n클로드가 반복 업무를 처리하는 흐름입니다.")
    assert "채널을 구독하세요" not in final
    assert final.endswith(shorts.fixed_cta(12))
    assert transform["status"] == "cta_only"
    assert transform["body_sha256_before"] == transform["body_sha256_after"]
    assert transform["parser_normalized_body_sha256"]
    assert transform["adopted_body_sha256"] == transform["final_adopted_body_sha256"]
    ledger = json.loads((tmp_path / "attempt-ledger.json").read_text(encoding="utf-8"))
    assert ledger["attempts"][0]["attempt_status"] == "passed"


def test_numeric_sixth_is_cut():
    original = "이 프로그램 대박입니다.\n1. 하나\n2. 둘\n3. 셋\n4. 넷\n5. 다섯\n6. 여섯"
    assert "6. 여섯" not in shorts.finalize_script(original, 10)


def test_fifth_item_paragraphs_are_preserved_until_an_explicit_cta():
    original = """이 프로그램 대박입니다.

첫째, 하나

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
1. 클로드가 디자인한다고? / 디자인 AI 티 지우는법
2. 이거 그냥 쓰면 손해 / 클로드 디자인 바꾸는법
3. 5단계면 충분합니다 / AI 모션그래픽 개선법

### 스크립트
이 프로그램 대박입니다.
다음 문장입니다.
"""
    candidates = shorts.extract_head_copy_candidates(answer)
    assert candidates == [
        "클로드가 디자인한다고?\n디자인 AI 티 지우는법",
        "이거 그냥 쓰면 손해\n클로드 디자인 바꾸는법",
        "5단계면 충분합니다\nAI 모션그래픽 개선법",
    ]


def test_extracts_plain_notebooklm_headings_without_markdown_hashes():
    answer = """헤드카피라이팅
1. 클로드가 디자인한다고? / 디자인 AI 티 지우는법
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
고딩이 돈을 벌었다고?! / 클로드로 자동 영업
제안서 쓰다 밤새워?! / 클로드로 5분 완성
매번 새로 가르쳐?! / 옵시디언에 기억 저장
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
        "클로드가 디자인한다고? / AI 티 지우는법", script
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


def test_head_copy_uses_exact_90px_font_width_to_prevent_three_visible_lines():
    evidence = policy.validate_headline_pixel_width(
        ("예산이 부족하다고?!", "내 몸값 깎지 마세요")
    )
    assert evidence["visible_line_count"] == 2
    assert max(evidence["line_widths_px"]) <= 920
    with pytest.raises(policy.ProductionPolicyError, match="안전폭"):
        policy.validate_headline_pixel_width(
            ("맨날 몸값 깎이고 덤핑당해?!", "AI 에이전시로 월 2만 불 받기")
        )
    with pytest.raises(RuntimeError, match="안전폭"):
        shorts.validate_head_copy(
            "맨날 몸값 깎이고 덤핑당해?! / AI 에이전시로 월 2만 불 받기"
        )


def test_shorts_title_removes_all_trailing_hashtags():
    assert shorts_video.normalize_shorts_title(
        "클로드 디자인 5단계 #AI #Shorts"
    ) == "클로드 디자인 5단계"
    assert shorts_video.normalize_shorts_title("클로드 디자인 5단계") == "클로드 디자인 5단계"
