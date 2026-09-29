"""대본으로 쓴 헤드카피 시험. 실제 에이전트 호출은 하지 않는다."""
from __future__ import annotations

from pathlib import Path

import pytest

import shorts_headcopy as headcopy
import shorts_new_prepare as prepare

SCRIPT = (
    "이 남자 미쳤습니다. 닉 사라에프가 세 시간 걸리던 제안서 작성을 이 분 만에 처리하는 "
    "클로드 루틴을 공개했습니다. 업무 자동화 5가지 방법, 저장하고 끝까지 보세요!\n\n"
    "첫째, 반복되는 제안서 양식을 클로드 스킬로 만들어 두세요."
)


def _answer(pairs):
    return {"candidates": [{"line1": a, "line2": b} for a, b in pairs]}


def test_written_headcopy_keeps_only_candidates_that_pass_the_gates(monkeypatch):
    import subscription_agent

    monkeypatch.setattr(subscription_agent, "run_json", lambda *a, **k: _answer([
        ("3시간을 2분으로", "클로드 제안서 루틴"),
        ("이 남자 미쳤습니다!", "클로드 제안서 루틴"),        # 음성이 말하는 문구
        ("가", "나"),                                        # 너무 짧다
    ]))
    heads = headcopy.write_headcopy(SCRIPT)
    assert heads == ["3시간을 2분으로\n클로드 제안서 루틴"]


def test_spelled_out_numbers_become_digits(monkeypatch):
    import subscription_agent

    monkeypatch.setattr(subscription_agent, "run_json", lambda *a, **k: _answer([
        ("세 시간을 이 분으로", "클로드 제안서 루틴"),
    ]))
    assert headcopy.write_headcopy(SCRIPT)[0].startswith("3시간을 2분으로")


def test_no_usable_candidate_is_an_error_so_the_caller_can_fall_back(monkeypatch):
    import subscription_agent

    monkeypatch.setattr(subscription_agent, "run_json", lambda *a, **k: _answer([
        ("이 프로그램 대박입니다!", "클로드 제안서 루틴"),
    ]))
    with pytest.raises(headcopy.HeadcopyError):
        headcopy.write_headcopy(SCRIPT)


def test_prepare_falls_back_to_notebook_candidates_when_the_agent_is_blocked(monkeypatch):
    """구독 한도가 막혀도 제작은 이어져야 한다. 헤드카피는 덜 좋아질 뿐이다."""
    def explode(*args, **kwargs):  # noqa: ANN002, ANN003
        raise headcopy.HeadcopyError("두 엔진이 모두 실패")

    monkeypatch.setattr(headcopy, "write_headcopy", explode)
    assert prepare._written_headcopy(SCRIPT) == []


def test_recent_headcopy_reads_the_first_line_of_each_candidate_file(tmp_path: Path):
    root = tmp_path / "outputs/aaa-20260101/shorts"
    root.mkdir(parents=True)
    (root / "06_headcopy_candidates.txt").write_text(
        "1. 제안서 3시간을 2분으로 / 클로드 업무 자동화\n2. 다른 안 / 둘째 줄\n", encoding="utf-8")
    assert headcopy.recent_headcopy(project=tmp_path) == ["제안서 3시간을 2분으로 / 클로드 업무 자동화"]
