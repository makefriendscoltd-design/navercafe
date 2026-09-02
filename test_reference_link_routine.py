import json
from pathlib import Path

import reference_link_routine as routine


def test_clean_url_normalizes_supported_youtube_forms():
    assert routine.clean_url("https://www.youtube.com/watch?v=abc_DEF-12&si=secret") == "https://youtu.be/abc_DEF-12"
    assert routine.clean_url("https://youtu.be/abc_DEF-12?t=20") == "https://youtu.be/abc_DEF-12"
    assert routine.clean_url("https://www.youtube.com/shorts/abc_DEF-12?feature=share") == "https://youtu.be/abc_DEF-12"


def test_scan_deduplicates_and_keeps_locations(tmp_path: Path):
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "one.md").write_text("# First\nhttps://youtu.be/abc_DEF-12\n", encoding="utf-8")
    (ref / "two.txt").write_text("https://www.youtube.com/watch?v=abc_DEF-12&si=x\nhttps://youtu.be/xyz98765\n", encoding="utf-8")
    items = routine.scan(ref)
    assert [item["source_key"] for item in items] == ["abc_DEF-12", "xyz98765"]
    assert items[0]["duplicate_count"] if "duplicate_count" in items[0] else len(items[0]["locations"]) == 2


def test_build_report_marks_existing_output(tmp_path: Path):
    ref = tmp_path / "ref"
    (ref).mkdir()
    (ref / "links.md").write_text("https://youtu.be/abc_DEF-12\n", encoding="utf-8")
    project = tmp_path / "project"
    (project / "outputs").mkdir(parents=True)
    (project / "outputs" / "manifest.json").write_text(json.dumps({"source_key": "abc_DEF-12"}), encoding="utf-8")
    report = routine.build_report(ref, project, tmp_path / "state.json")
    assert report["items"][0]["status"] == "already_in_outputs"
