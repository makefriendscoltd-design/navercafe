from pathlib import Path

import reference_discovery as discovery


def test_digest_is_explicitly_non_publishing(tmp_path: Path):
    out = discovery.write_digest(
        [{"kind": "similar_topic", "query": "AI agents", "channel": "Example", "title": "Demo", "video_id": "abc_DEF-12", "url": "https://youtu.be/abc_DEF-12"}],
        tmp_path,
        discovery.datetime.now().astimezone(),
    )
    text = out.read_text(encoding="utf-8")
    assert "제작·발행은 링크를 검토한 뒤" in text
    assert "https://youtu.be/abc_DEF-12" in text
