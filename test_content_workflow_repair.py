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
