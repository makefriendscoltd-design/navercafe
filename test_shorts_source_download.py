import json
import sys
import types
from pathlib import Path

import pytest

import shorts_new_prepare as prepare


def test_download_prefers_18_but_allows_mp4_fallback(monkeypatch, tmp_path):
    captured = {}
    stale = tmp_path / '.source-download-old'
    stale.mkdir()
    (stale / 'source.mp4.part').write_bytes(b'prior-interrupted-download')

    class FakeYoutubeDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, url, download):
            output = Path(captured["outtmpl"].replace("%(ext)s", "mp4"))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"downloaded-video")
            return {"id": "abcdefghijk", "channel": "Source Channel", "title": "Talk",
                    "duration": 900, "format_id": "137+140"}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    monkeypatch.setattr(prepare.subprocess, "run", lambda *args, **kwargs:
                        types.SimpleNamespace(stdout="video\n"))

    video, credit = prepare._download_source("abcdefghijk", tmp_path)

    assert captured["format"].startswith("18/")
    assert captured["merge_output_format"] == "mp4"
    assert captured["extractor_args"]["youtube"]["player_client"] == ["mweb"]
    assert video.read_bytes() == b"downloaded-video"
    evidence = json.loads((tmp_path / "source_download_evidence.json").read_text())
    assert evidence["selected_format_id"] == "137+140"
    assert evidence["source"]["sha256"]
    assert credit == "출처: Source Channel"
    assert (stale / 'source.mp4.part').read_bytes() == b'prior-interrupted-download'


def test_download_refuses_partial_prior_state(tmp_path):
    (tmp_path / "source_original.mp4").write_bytes(b"do-not-overwrite")
    with pytest.raises(RuntimeError, match="덮어쓰지 않습니다"):
        prepare._download_source("abcdefghijk", tmp_path)
