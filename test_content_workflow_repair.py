from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from content_queue_guard import select_entry
import youtube_cardnews_pipeline as pipeline


def test_queue_never_selects_uncertain_or_already_published_entries():
    now = datetime(2026, 9, 6, 18, tzinfo=ZoneInfo('Asia/Seoul'))
    entry = {'source_key': 'x', 'status': 'pending', 'attempts': 0, 'not_before': now.isoformat()}
    for changes in ({'do_not_retry': True}, {'published_url': 'https://cafe.naver.com/x/1'},
                    {'status': 'blocked'}, {'status': 'reconcile_required'}):
        assert select_entry({'entries': [{**entry, **changes}]}, now) is None
    assert select_entry({'entries': [entry]}, now) == entry


def test_queue_prefers_unattempted_due_item_without_changing_short_schedule():
    now = datetime(2026, 9, 6, 18, tzinfo=ZoneInfo('Asia/Seoul'))
    first = {'source_key': 'a', 'status': 'failed', 'next_eligible_at': now.isoformat()}
    second = {'source_key': 'b', 'status': 'pending', 'not_before': now.isoformat(), 'shorts_scheduled_at': 'keep'}
    assert select_entry({'entries': [first, second]}, now) is second
    assert second['shorts_scheduled_at'] == 'keep'


def test_empty_card_generation_cannot_be_filled_with_invented_content():
    with pytest.raises(RuntimeError, match='자동 보충하지'):
        pipeline.normalize_deck({'slides': []}, 'input', 'title')


def test_approved_card_renderer_displays_closing_title_from_generation_schema():
    import cardnews_renderer
    builder = cardnews_renderer.load_builder()
    markup = builder.slide_html(9, {'layoutType': 'cta', 'f': {
        'title': '마감 제목 확인', 'sub': '마감 본문 확인', 'cta1': '댓글 AIMAX', 'cta2': '관련 정보 받기'
    }}, '검증용 주제')
    assert '마감 제목 확인' in markup
    assert '마감 본문 확인' in markup


def test_card_generation_requires_bound_notebooklm_input_before_api_call():
    with pytest.raises(RuntimeError, match='NotebookLM'):
        pipeline.make_card_deck('arbitrary text', 'title')


def test_legacy_pipeline_cannot_bypass_channel_contracts():
    with pytest.raises(RuntimeError, match='레거시'):
        pipeline.main([])


def test_common_v7_builder_blocks_before_tts_on_unbound_source(tmp_path, monkeypatch):
    import shorts_v7_builder as builder
    calls = []
    monkeypatch.setattr(builder, 'make_audio_and_captions', lambda: calls.append('tts'))
    (tmp_path / 'production_manifest.json').write_text('{"source_id":"test"}')
    with pytest.raises(ValueError, match='notebooklm_verbatim'):
        builder.main(['--root', str(tmp_path), '--render'])
    assert calls == []


def test_preserved_interrupted_cafe_answer_can_rebuild_but_not_approve_replacement(tmp_path):
    import json
    from content_workflow import prepare_cafe
    from content_lineage import cafe_body_lineage
    cafe = tmp_path / 'cafe'
    (cafe / 'notebooklm').mkdir(parents=True)
    answer = cafe / 'notebooklm/notebooklm-answer.md'
    answer.write_text('\n\n'.join(f'## 원본 소제목 {i}\n\n원본 설명을 그대로 보존합니다.\n\n제작자의 설명입니다.' for i in range(5)))
    manifest = cafe / '06_cafe_manifest.json'
    manifest.write_text(json.dumps({'source_key': 'example1234', 'body_file': 'old.txt',
        'images': ['a.jpg'] * 5, 'notebooklm_recovery_evidence': 'recovered.json'}))
    (cafe / 'recovered.json').write_text(json.dumps({'account': 'u0', 'notebookTitle': '민수대표님_카페글',
        'bindingOk': True, 'answerDone': True, 'answerChars': len(answer.read_text())}))
    (cafe / '11_local_validation.json').write_text(json.dumps({'checks': {'notebook_answer_recovered': True}}))
    candidate = tmp_path / 'candidate'
    assert prepare_cafe(manifest, candidate)['status'] == 'pass'
    assert cafe_body_lineage(answer, candidate / '03_cafe_body.txt')['body_preserved']
    replacement = tmp_path / 'replacement.md'
    replacement.write_text(answer.read_text())
    with pytest.raises(ValueError, match='source binding'):
        prepare_cafe(manifest, tmp_path / 'unapproved', replacement)


def test_a_community_post_must_follow_the_owner_structure():
    """Bracket hook, numbered sections, 결론, and the fixed closing line."""
    import youtube_cardnews_pipeline as pipeline

    good = (
        "[단돈 1달러로 영화 같은 웹사이트를 만들 수 있습니다] Kimi K3 실전 가이드를 정리했습니다.\n\n"
        "스크롤할 때마다 한 편의 영화처럼 전개되는 웹사이트를 본 적 있으신가요? "
        "과거엔 수천 달러와 전문 개발자가 필요했습니다. "
        "이제는 단돈 1~2달러면 가능합니다. "
        "만드는 법을 정리했습니다.\n\n"
        "----\n\n1. Kimi K3가 뭔가요\n\n저렴한 비용으로 강력한 성능을 제공하는 모델입니다.\n\n"
        "----\n\n2. 매크로 여정 영상이 핵심입니다\n\n8~10초 분량으로 설정합니다.\n\n"
        "----\n\n3. 시네마틱 스튜디오로 만드세요\n\n프롬프트를 입력하면 영상이 생성됩니다.\n\n"
        "----\n\n4. 60fps로 보간해야 부드러워집니다\n\n프레임을 두 배로 늘립니다.\n\n"
        "----\n\n결론은 코딩 실력이 없어도 된다는 것입니다. "
        "중요한 건 어떤 여정을 선사할지에 대한 선택입니다. "
        "오늘 하나 만들어 보세요.\n"
        + pipeline.YOUTUBE_FINAL_LINE
    )
    assert pipeline.validate_youtube_post(good)["sections"] == 4

    import pytest

    for broken, why in [
        (good.replace("[단돈 1달러로 영화 같은 웹사이트를 만들 수 있습니다] ", ""), "대괄호"),
        (good.replace("결론은", "마무리는"), "결론"),
        (good.replace(pipeline.YOUTUBE_FINAL_LINE, "감사합니다."), "마지막 문장"),
        (good.replace("1. Kimi K3가 뭔가요", "Kimi K3가 뭔가요"), "섹션"),
    ]:
        with pytest.raises(RuntimeError, match="지정 구조와 다릅니다"):
            pipeline.validate_youtube_post(broken)


def test_a_forever_locked_queue_is_not_reported_as_merely_idle():
    """`no_due_entry` hid a queue where nothing could ever be selected again."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import content_queue_guard as guard

    now = datetime(2026, 9, 9, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    queue = {"entries": [
        {"source_key": "done", "status": "published",
         "published_url": "https://cafe.naver.com/westudyssat/1"},
        {"source_key": "later", "status": "pending", "attempts": 0,
         "not_before": "2026-09-10T10:00:00+09:00"},
        # Pending never gets picked once attempts>0, so this one waits forever.
        {"source_key": "mislabelled", "status": "pending", "attempts": 1,
         "next_eligible_at": "2026-09-08T11:00:00+09:00",
         "last_error": "AsideLoginRequired"},
        {"source_key": "locked", "status": "pending", "attempts": 0,
         "not_before": "2026-09-01T10:00:00+09:00", "do_not_retry": True},
    ]}
    queue["entries"].append(
        {"source_key": "not_longform", "status": "blocked", "do_not_retry": True,
         "last_error": "source_is_not_longform"})
    state = guard.backlog(queue, now)
    assert state["by_reason"]["blocked"] == 1
    assert "not_longform" not in state["permanently_stuck"]
    queue["entries"].pop()
    state = guard.backlog(queue, now)
    assert state["unpublished"] == 3
    assert state["by_reason"] == {"waiting": 1, "pending_with_attempts": 1, "do_not_retry": 1}
    assert state["permanently_stuck"] == ["locked", "mislabelled"]

    queue["entries"][2]["status"] = "failed"
    queue["entries"][3]["do_not_retry"] = False
    healthy = guard.backlog(queue, now)
    assert healthy["permanently_stuck"] == []
    assert healthy["by_reason"] == {"waiting": 1, "due": 2}


def test_one_named_channel_is_not_cross_platform_distribution():
    """Outreach advice picks a single channel; the gate read that as publishing."""
    import content_production_policy as policy

    single = ("내 서비스에 적합한 단 하나의 소통 채널을 고른 뒤 상대방의 고민에 맞춘 "
              "메시지를 꾸준한 물량으로 전송해야 미팅 기회가 열립니다.")
    assert policy.find_forbidden_shorts_claims(single) == {}
    assert policy.find_forbidden_shorts_claims("적합한 단일 소통 채널을 고른 뒤 맞춤 메시지를 전송함") == {}

    # Spraying several platforms is still exactly what the category catches.
    spray = "제작한 영상을 인스타그램과 틱톡, 링크드인에 자동으로 배포합니다."
    assert "automatic_cross_platform_distribution" in policy.find_forbidden_shorts_claims(spray)
    many = "여러 채널에 한 번에 게시합니다."
    assert "automatic_cross_platform_distribution" in policy.find_forbidden_shorts_claims(many)


def test_a_source_that_holds_one_slide_fails_the_render(tmp_path):
    """A 50-minute webinar sitting on one slide rendered as a dead short."""
    from PIL import Image
    import shorts_v7_builder as builder

    def frame(name, shade, *, noisy=False):
        path = tmp_path / name
        image = Image.new("L", (1080, 1920), 0)
        panel = Image.new("L", (1080, 900), shade)
        if noisy:
            panel.putpixel((5, 5), 255)
            panel.paste(Image.new("L", (400, 400), 255 - shade), (100, 100))
        image.paste(panel, (0, 340))
        image.save(path)
        return path

    varied = [frame(f"v{i}.png", 20 + i * 25, noisy=True) for i in range(8)]
    assert builder.still_source_group(varied)["largest_identical_group"] <= builder.MAX_IDENTICAL_CHECKPOINTS

    # Five checkpoints on the same slide, three elsewhere.
    static = [frame(f"s{i}.png", 90) for i in range(5)]
    static += [frame(f"s{i}.png", 20 + i * 60, noisy=True) for i in range(5, 8)]
    verdict = builder.still_source_group(static)
    assert verdict["largest_identical_group"] == 5
    assert verdict["largest_identical_group"] > builder.MAX_IDENTICAL_CHECKPOINTS
    assert verdict["example_frame"] in {str(p) for p in static}


def test_a_short_is_not_a_cafe_column_source():
    """A 54-second vertical Short was summarised into a Cafe post and published."""
    import pytest

    from content_production_policy import (
        MIN_LONGFORM_SOURCE_SECONDS, ProductionPolicyError, validate_longform_source)

    talk = validate_longform_source({"seconds": 495, "width": 640, "height": 360})
    assert talk["seconds"] == 495 and talk["minimum_seconds"] == MIN_LONGFORM_SOURCE_SECONDS

    with pytest.raises(ProductionPolicyError, match="세로 영상"):
        validate_longform_source({"seconds": 54, "width": 360, "height": 640})
    # Horizontal but far too short to hold five copyable steps.
    with pytest.raises(ProductionPolicyError, match="롱폼 기준"):
        validate_longform_source({"seconds": 453, "width": 640, "height": 360})
    with pytest.raises(ProductionPolicyError, match="측정하지 못했"):
        validate_longform_source({"seconds": 0, "width": 0, "height": 0})


def test_a_shorts_link_never_becomes_a_cafe_candidate(monkeypatch, tmp_path):
    """The gate has to stop at prepare time too, not only at publish time."""
    import pytest

    import cafe_new_prepare as prepare
    from content_production_policy import ProductionPolicyError

    root = tmp_path / "outputs" / "pw8Bt97U6fk-20260902"
    (root / "cafe/notebooklm").mkdir(parents=True)
    monkeypatch.setattr(prepare, "PROJECT", tmp_path)
    monkeypatch.setattr("cafe_manifest_publisher.measure_source_video",
                        lambda source_key: {"seconds": 54, "width": 360, "height": 640})

    with pytest.raises(ProductionPolicyError, match="세로 영상"):
        prepare.prepare("pw8Bt97U6fk")


def test_a_shorts_link_never_becomes_a_short(monkeypatch, tmp_path):
    """Ten Shorts on the channel were built from other people's Shorts."""
    import pytest

    import shorts_new_prepare as prepare
    from content_production_policy import ProductionPolicyError

    monkeypatch.setattr(prepare, "PROJECT", tmp_path)
    monkeypatch.setattr(prepare, "_source_root", lambda key: tmp_path / f"{key}-20260909")
    monkeypatch.setattr("cafe_manifest_publisher.measure_source_video",
                        lambda source_key: {"seconds": 27, "width": 360, "height": 640})

    with pytest.raises(ProductionPolicyError, match="세로 영상"):
        prepare.prepare("p3NBGLYVp8s")
