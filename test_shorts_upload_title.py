"""업로드 제목 규칙 시험. 제목 생성은 외부 호출이라 규칙과 폴백만 본다."""
from __future__ import annotations

import json
from pathlib import Path

import shorts_upload_title as titles


def test_rejects_headcopy_worn_openers_and_duplicates():
    taken = {"클로드 코드를 내 두 번째 뇌로 쓰는 법"}
    assert titles.check_title("화면에 나오는 그 문장", headcopy="화면에 나오는 그 문장", taken=taken)
    assert titles.check_title("이 남자 미쳤습니다", headcopy="다른 문장", taken=taken)
    assert titles.check_title("클로드 코드를 내 두 번째 뇌로 쓰는 법", headcopy="다른 문장", taken=taken)
    assert titles.check_title("월 500만원 보장되는 자동화", headcopy="다른 문장", taken=taken)
    assert titles.check_title("가" * (titles.MAX_TITLE_CHARS + 1), headcopy="다른 문장", taken=taken)
    assert titles.check_title("자동화 근황 #쇼츠", headcopy="다른 문장", taken=taken)
    assert titles.check_title("", headcopy="다른 문장", taken=taken) is not None
    assert titles.check_title("장부 정리를 클로드에 맡기는 법", headcopy="다른 문장", taken=taken) is None


def test_rejects_a_third_title_ending_in_the_same_word():
    taken = {"업무 자동화 5가지 비결", "메일 정리 3가지 비결"}
    assert titles.check_title("장부 관리 자동화 비결", headcopy="다른 문장", taken=taken)
    assert titles.check_title("장부 관리를 맡기는 법", headcopy="다른 문장", taken=taken) is None


def test_used_titles_reads_both_ledgers(tmp_path: Path):
    a = tmp_path / "AAA-20260101/shorts"
    b = tmp_path / "BBB-20260101/shorts"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / titles.TITLE_FILENAME).write_text(json.dumps({"title": "먼저 쓴 제목"}), encoding="utf-8")
    (b / "07_provider_manifest.json").write_text(json.dumps({"title": "올라간 제목"}), encoding="utf-8")
    (tmp_path / "CCC-20260101").mkdir()
    assert titles.used_titles(tmp_path) == {"먼저 쓴 제목", "올라간 제목"}


def test_upload_title_falls_back_instead_of_blocking_publish(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(titles, "write_title", lambda root: (_ for _ in ()).throw(RuntimeError("모델 장애")))
    assert titles.upload_title(tmp_path, "헤드카피 제목") == "헤드카피 제목"
    assert "모델 장애" in (tmp_path / "upload_title_error.txt").read_text(encoding="utf-8")


def test_existing_title_is_reused_so_republishing_keeps_it(tmp_path: Path):
    root = tmp_path / "shorts"
    root.mkdir()
    (root / "production_manifest.json").write_text(
        json.dumps({"render_inputs": {"upload_title": "헤드카피"}}), encoding="utf-8")
    (root / titles.TITLE_FILENAME).write_text(
        json.dumps({"title": "이미 정한 제목"}), encoding="utf-8")
    record = titles.write_title(root)
    assert record["title"] == "이미 정한 제목" and record["status"] == "existing"
