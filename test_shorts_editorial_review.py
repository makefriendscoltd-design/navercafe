from __future__ import annotations

import hashlib
import json

import shorts_editorial_review as review


def _root(tmp_path):
    shorts = tmp_path / "shorts"
    (shorts / "captions").mkdir(parents=True)
    (shorts / "07_script_final.txt").write_text(
        "도입\n\n첫째 본문\n\n둘째 본문\n\n셋째 본문\n\n넷째 본문\n\n다섯째 본문",
        encoding="utf-8")
    transcript = "Alpha exact evidence.\nBeta exact evidence.\nGamma exact evidence."
    (shorts / "captions/transcript.txt").write_text(transcript, encoding="utf-8")
    return shorts, transcript


def _answer(line_range=(1, 1)):
    return {"sections": [{"section": name, "supported": True,
                           "source_line_ranges": [list(line_range)],
                           "explanation": "actor, task, modality, numbers match"}
                          for name in review.SECTIONS], "summary": "checked"}


def test_invalid_source_line_range_rejects_every_section(tmp_path, monkeypatch):
    shorts, _ = _root(tmp_path)
    monkeypatch.setattr("subscription_agent.run_json", lambda *args, **kwargs: _answer((99, 100)))
    report = review.review(shorts)
    assert report["status"] == "fail"
    assert report["failed_sections"] == list(review.SECTIONS)
    assert all(row["quote_exact"] is False for row in report["sections"])


def test_valid_line_ranges_extract_exact_quotes_and_bind_inputs(tmp_path, monkeypatch):
    shorts, transcript = _root(tmp_path)
    monkeypatch.setattr("subscription_agent.run_json",
                        lambda *args, **kwargs: _answer((1, 2)))
    report = review.review(shorts)
    assert report["status"] == "pass"
    assert report["script_sha256"] == hashlib.sha256(
        (shorts / "07_script_final.txt").read_bytes()).hexdigest()
    assert report["transcript_sha256"] == hashlib.sha256(transcript.encode()).hexdigest()
    assert report["sections"][0]["source_quotes"] == [
        "Alpha exact evidence.\nBeta exact evidence."]
    assert json.loads((shorts / "editorial_review.json").read_text())["status"] == "pass"


def test_failed_review_is_not_reused_but_matching_pass_is(tmp_path, monkeypatch):
    shorts, _ = _root(tmp_path)
    calls = []
    monkeypatch.setattr("subscription_agent.run_json",
                        lambda *args, **kwargs: (calls.append(1) or _answer((0, 1))))
    assert review.review(shorts)["status"] == "fail"
    assert review.review(shorts)["status"] == "fail"
    assert len(calls) == 2

    monkeypatch.setattr("subscription_agent.run_json",
                        lambda *args, **kwargs: (calls.append(1) or _answer((1, 1))))
    assert review.review(shorts)["status"] == "pass"
    assert review.review(shorts)["status"] == "pass"
    assert len(calls) == 3


def test_missing_transcript_writes_pending_without_agent_call(tmp_path, monkeypatch):
    shorts = tmp_path / "shorts"
    shorts.mkdir()
    (shorts / "07_script_final.txt").write_text("대본", encoding="utf-8")
    monkeypatch.setattr("subscription_agent.run_json", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("로컬 전사문이 없으면 에이전트를 부르면 안 된다")))
    report = review.review(shorts)
    assert report["status"] == "pending"


def test_headcopy_and_multiple_source_passages_are_checked(tmp_path, monkeypatch):
    shorts, _ = _root(tmp_path)
    (shorts / "06_headcopy_candidates.txt").write_text("검토할 헤드카피")
    answer = _answer((1, 1))
    answer["sections"].append({"section": "headcopy", "supported": True,
                               "source_line_ranges": [[1, 1], [3, 3]]})
    monkeypatch.setattr("subscription_agent.run_json", lambda *args, **kwargs: answer)
    assert review.review(shorts)["status"] == "pass"
    (shorts / "06_headcopy_candidates.txt").write_text("새로운 근거 없는 수치")
    answer["sections"][-1]["source_line_ranges"] = [[30, 30]]
    result = review.review(shorts)
    assert result["status"] == "fail"
    assert result["failed_sections"] == ["headcopy"]


def test_fixed_channel_cta_is_not_mistaken_for_source_claim():
    prompt = review._prompt("다섯째, 원문 행동.\n\n12분 짜리 영상 내용을 모두 정리했습니다.\n\n이 자료 궁금하신 분들은 댓글에 조사 남겨주세요.", "source")
    assert "댓글에 조사" not in prompt
    assert "다섯째, 원문 행동." in prompt
