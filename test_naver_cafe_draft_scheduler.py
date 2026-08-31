import configparser
import json

import naver_cafe_draft_scheduler as scheduler


def _manifest(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({
        "title": "정확한 제목",
        "cafe_url": "https://cafe.naver.com/ca-fe/cafes/1/menus/2",
        "board_name": "AI 자동화&수익화 정보",
        "expected_images": 3,
        "expected_quotes": 2,
        "expected_sequence": ["quote", "text", "image", "text"],
        "expected_quote_texts": ["첫 제목", "둘째 제목"],
        "cta_link_url": "https://cafe.naver.com/example/1",
        "source_url": "https://youtu.be/example",
        "source_key": "example",
    }, ensure_ascii=False), encoding="utf-8")
    return path


def test_run_manifest_tracks_only_after_provider_proof(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "_load_config", configparser.RawConfigParser)
    monkeypatch.setattr(scheduler, "publish_saved_naver_cafe_draft", lambda *a, **k: {
        "status": "published", "url": "https://cafe.naver.com/example/2"
    })
    tracked = []
    monkeypatch.setattr(scheduler, "track_external_event", lambda *a, **k: tracked.append((a, k)) or True)
    result_path = tmp_path / "result.json"
    result = scheduler.run_manifest(_manifest(tmp_path), result_path)
    assert result["status"] == "published"
    assert tracked[0][0] == ("naver_cafe", "example")
    assert tracked[0][1]["stage"] == "sent"
    assert json.loads(result_path.read_text(encoding="utf-8"))["status"] == "published"


def test_run_manifest_blocks_and_notifies_without_tracking(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "_load_config", configparser.RawConfigParser)
    monkeypatch.setattr(
        scheduler,
        "publish_saved_naver_cafe_draft",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("구조 불일치")),
    )
    monkeypatch.setattr(scheduler, "_notify_failure", lambda *a, **k: True)
    monkeypatch.setattr(
        scheduler,
        "track_external_event",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent 추적 금지")),
    )
    result = scheduler.run_manifest(_manifest(tmp_path), tmp_path / "result.json")
    assert result["status"] == "blocked"
    assert result["ccida_private_notified"] is True
