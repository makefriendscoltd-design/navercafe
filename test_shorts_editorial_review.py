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
    transcript = "Alpha exact evidence. Beta exact evidence. Gamma exact evidence."
    (shorts / "captions/transcript.txt").write_text(transcript, encoding="utf-8")
    return shorts, transcript


def _answer(quote):
    return {"sections": [{"section": name, "supported": True, "source_quote": quote,
                           "explanation": "actor, task, modality, numbers match"}
                          for name in review.SECTIONS], "summary": "checked"}


def test_hallucinated_source_quote_rejects_every_section(tmp_path, monkeypatch):
    shorts, _ = _root(tmp_path)
    monkeypatch.setattr("subscription_agent.run_json", lambda *args, **kwargs: _answer("invented quote"))
    report = review.review(shorts)
    assert report["status"] == "fail"
    assert report["failed_sections"] == list(review.SECTIONS)
    assert all(row["quote_exact"] is False for row in report["sections"])


def test_exact_quotes_pass_and_bind_script_and_transcript(tmp_path, monkeypatch):
    shorts, transcript = _root(tmp_path)
    monkeypatch.setattr("subscription_agent.run_json",
                        lambda *args, **kwargs: _answer("Alpha   exact evidence."))
    report = review.review(shorts)
    assert report["status"] == "pass"
    assert report["script_sha256"] == hashlib.sha256(
        (shorts / "07_script_final.txt").read_bytes()).hexdigest()
    assert report["transcript_sha256"] == hashlib.sha256(transcript.encode()).hexdigest()
    assert json.loads((shorts / "editorial_review.json").read_text())["status"] == "pass"


def test_failed_review_is_not_reused_but_matching_pass_is(tmp_path, monkeypatch):
    shorts, _ = _root(tmp_path)
    calls = []
    monkeypatch.setattr("subscription_agent.run_json",
                        lambda *args, **kwargs: (calls.append(1) or _answer("invented")))
    assert review.review(shorts)["status"] == "fail"
    assert review.review(shorts)["status"] == "fail"
    assert len(calls) == 2

    monkeypatch.setattr("subscription_agent.run_json",
                        lambda *args, **kwargs: (calls.append(1) or _answer("Alpha exact evidence.")))
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
    answer = _answer("Alpha exact evidence.")
    answer["sections"].append({"section": "headcopy", "supported": True,
                               "source_quotes": ["Alpha exact evidence.", "Gamma exact evidence."]})
    monkeypatch.setattr("subscription_agent.run_json", lambda *args, **kwargs: answer)
    assert review.review(shorts)["status"] == "pass"
    (shorts / "06_headcopy_candidates.txt").write_text("새로운 근거 없는 수치")
    answer["sections"][-1]["source_quotes"] = ["invented number"]
    result = review.review(shorts)
    assert result["status"] == "fail"
    assert result["failed_sections"] == ["headcopy"]
