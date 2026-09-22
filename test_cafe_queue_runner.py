import json
from datetime import datetime, timezone

import pytest

import cafe_queue_runner as runner


@pytest.mark.parametrize("url", [
    "https://cafe.naver.com/westudyssat/6168",
    "https://cafe.naver.com/f-e/cafes/26321967/articles/6168",
    "https://cafe.naver.com/westudyssat?iframe_url_utf8=%2FArticleRead.nhn%253Fclubid%3D26321967%2526articleid%3D6168",
])
def test_provider_receipt_urls_reconcile_to_same_article(tmp_path, url):
    (tmp_path / "evidence.json").write_text(json.dumps({"providerUrl": url}))
    assert runner.read_provider_url(tmp_path, {"provider_evidence": "evidence.json"}) == \
        "https://cafe.naver.com/westudyssat/6168"


def test_missing_provider_url_cannot_mark_queue_success(tmp_path):
    path = tmp_path / "queue.json"
    original = {"entries": [{"source_key": "source", "status": "failed"}]}
    path.write_text(json.dumps(original))
    with pytest.raises(RuntimeError, match="no verified provider"):
        runner.record(tmp_path, path, "source", datetime.now(timezone.utc), True, "")
    assert json.loads(path.read_text()) == original
