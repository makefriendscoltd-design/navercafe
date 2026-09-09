import importlib.util
import html
import json
import re
from pathlib import Path


BUILDER = Path("outputs/20260822-shorts-correction-audit/cardnews/"
               "square-carousel-v2-20260823/build_square_carousel.py")


def load_builder():
    spec = importlib.util.spec_from_file_location("square_builder_emphasis_test", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_paired_inline_emphasis_becomes_safe_strong_markup():
    got = load_builder().esc("나만의 AI 비서 *팀* 만들기")
    assert got == '나만의 AI 비서 <strong class="inline-emphasis">팀</strong> 만들기'


def test_unpaired_stars_and_url_path_are_preserved_as_text():
    builder = load_builder()
    assert builder.esc("짝 없는 *표시") == "짝 없는 *표시"
    assert builder.esc("https://example.com/*raw*/x") == "https://example.com/*raw*/x"


def test_emphasis_content_is_html_escaped():
    got = load_builder().esc("확인 *<script>* 완료")
    assert "<script>" not in got
    assert "&lt;script&gt;" in got


def test_korean_particles_may_touch_emphasis_markers():
    got = load_builder().esc("점진적 *피드백*으로 훈련하기")
    assert "*" not in got
    assert '<strong class="inline-emphasis">피드백</strong>으로' in got


def test_backslash_escaped_literal_stars_are_preserved():
    assert load_builder().esc(r"문자 \*그대로\* 유지") == r"문자 \*그대로\* 유지"


def test_real_deck_emphasis_preserves_words_and_removes_only_markers():
    builder = load_builder()
    deck = json.loads(Path("outputs/4hKJ9X6rGFo-20260907/cardnews/04_cardnews_deck.json")
                      .read_text(encoding="utf-8"))
    checked = 0
    for index, slide in enumerate(deck["slides"]):
        slide["layoutType"] = builder.layout_for(index, slide)
        visible = html.unescape(re.sub(r"<[^>]+>", "", builder.slide_html(index, slide, "topic")))
        for value in slide.get("f", {}).values():
            if isinstance(value, str) and "*" in value:
                expected = builder.plain_display_text(value)
                assert expected in visible
                assert value not in visible
                checked += 1
    assert checked == 10
