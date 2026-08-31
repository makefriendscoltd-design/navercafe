import pytest

from content_factcheck import FactCheckError, apply_corrections
from youtube_cardnews_pipeline import deck_validation_issues


def test_applies_exact_correction_without_rewriting_other_text():
    text = "도입 문장. GPT 5.4가 최신입니다. 끝 문장."
    result = apply_corrections(
        text,
        [{"old": "GPT 5.4가 최신입니다.", "new": "해당 최신 모델 표현은 삭제했습니다."}],
    )
    assert result == "도입 문장. 해당 최신 모델 표현은 삭제했습니다. 끝 문장."


def test_missing_exact_source_blocks_instead_of_guessing():
    with pytest.raises(FactCheckError):
        apply_corrections("원고", [{"old": "없는 문장", "new": "새 문장"}])


def _valid_deck():
    slides = [{"type": "cover", "f": {"title": "AI 티를 없애는 디자인 5단계"}}]
    for index in range(1, 9):
        slides.append({
            "type": "content",
            "f": {
                "head": f"디자인 차별화 방법 {index}",
                "desc": f"검증된 원문에서 {index}번째 실행 방법을 구체적으로 정리했습니다.",
            },
        })
    slides.append({"type": "closing", "f": {"head": "댓글에 AIMAX"}})
    return {"slides": slides}


def test_card_deck_validation_rejects_cut_sentence_and_duplicate_heads():
    deck = _valid_deck()
    deck["slides"][2]["f"]["head"] = deck["slides"][1]["f"]["head"]
    deck["slides"][3]["f"]["desc"] = "문장이 중간에서"
    issues = deck_validation_issues(deck)
    assert any("문장이 아님" in issue for issue in issues)
    assert any("아이디어 중복" in issue for issue in issues)


def test_card_deck_validation_accepts_complete_ten_cards():
    assert deck_validation_issues(_valid_deck()) == []


def test_card_deck_validation_accepts_quote_and_table_middle_cards():
    deck = _valid_deck()
    deck["slides"][2] = {
        "type": "quote",
        "f": {"quote": "폰트 하나가 AI 티를 결정합니다.", "by": "핵심 요약"},
    }
    deck["slides"][3] = {
        "type": "table",
        "f": {
            "title": "기본 생성과 시스템 적용 비교",
            "rows": "폰트 | 기본값 | 브랜드 폰트\n자산 | 자체 생성 | 검증된 아이콘",
        },
    }
    assert deck_validation_issues(deck) == []
