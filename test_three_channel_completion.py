import json
from pathlib import Path

import youtube_cardnews_pipeline as pipeline


def test_shorts_script_alone_never_completes_a_link():
    assert not pipeline.three_channel_complete(
        {
            "source_id": "GExjqEBXKN4",
            "cafe_published": True,
            "youtube_published": True,
            "shorts_script_file": "07_shorts_script_through_fifth.txt",
        }
    )


def test_completion_requires_all_three_provider_confirmations():
    state = {
        "source_id": "GExjqEBXKN4",
        "cafe_published": True,
        "cafe_source_id": "GExjqEBXKN4",
        "cafe_url": "https://cafe.naver.com/example/1",
        "youtube_published": True,
        "youtube_source_id": "GExjqEBXKN4",
        "youtube_url": "https://www.youtube.com/post/example",
        "shorts_published": True,
        "shorts_source_id": "GExjqEBXKN4",
        "shorts_url": "https://www.youtube.com/shorts/example",
    }
    assert pipeline.three_channel_complete(state)


def test_completion_rejects_crossed_source_channels():
    state = {
        "source_id": "GExjqEBXKN4",
        "cafe_published": True,
        "cafe_source_id": "GExjqEBXKN4",
        "cafe_url": "https://cafe.naver.com/example/1",
        "youtube_published": True,
        "youtube_source_id": "another-source",
        "youtube_url": "https://www.youtube.com/post/example",
        "shorts_published": True,
        "shorts_source_id": "GExjqEBXKN4",
        "shorts_url": "https://www.youtube.com/shorts/example",
    }
    assert not pipeline.three_channel_complete(state)


def test_bundle_status_is_atomic_and_resumable(tmp_path: Path):
    state = {
        "fingerprint": "abc",
        "cafe_published": True,
        "youtube_published": False,
        "shorts_published": False,
    }
    path = pipeline.save_bundle_publish_status(tmp_path, state)
    assert json.loads(path.read_text(encoding="utf-8")) == state
    assert pipeline.load_bundle_publish_status(tmp_path) == state
    assert not path.with_suffix(".json.tmp").exists()


def test_publish_fingerprint_ignores_youtube_share_query():
    first = pipeline.publish_fingerprint(
        "https://youtu.be/RDytbVDzMF4?si=first", "제목", "카페", "카드", "쇼츠"
    )
    second = pipeline.publish_fingerprint(
        "https://www.youtube.com/watch?v=RDytbVDzMF4&si=second",
        "제목", "카페", "카드", "쇼츠",
    )
    assert first == second
