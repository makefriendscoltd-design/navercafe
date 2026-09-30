import json

import pytest

import youtube_shorts_ego as ego


def test_backend_defaults_to_ego(monkeypatch):
    monkeypatch.delenv("YOUTUBE_SHORTS_BROWSER", raising=False)
    assert ego.backend() == "ego"
    monkeypatch.setenv("YOUTUBE_SHORTS_BROWSER", "bogus")
    with pytest.raises(ego.EgoRuntimeError):
        ego.backend()


def test_compose_embeds_payload_config_and_body(tmp_path, monkeypatch):
    space = tmp_path / "space.json"
    space.write_text(json.dumps({"spaceId": 5}))
    monkeypatch.setattr(ego, "SPACE_FILE", space)
    script = ego.compose("emit({status:'pass'});", {"a": "나민수"}, cwd=tmp_path)
    assert '{"a": "나민수"}' in script
    assert '"spaceId": 5' in script
    assert "emit({status:'pass'});" in script
    assert "__YT_EGO_" not in script


def test_space_must_be_initialized(tmp_path, monkeypatch):
    monkeypatch.setattr(ego, "SPACE_FILE", tmp_path / "missing.json")
    with pytest.raises(ego.EgoRuntimeError):
        ego.space_id()


def test_file_transfer_is_staged_with_bound_digest(tmp_path, monkeypatch):
    import hashlib

    video = tmp_path / "final.mp4"
    video.write_bytes(b"video-bytes")
    digest = hashlib.sha256(b"video-bytes").hexdigest()
    seen = {}
    monkeypatch.setattr(ego, "resolve_ego_cli", lambda: "/bin/true")
    monkeypatch.setattr(ego, "space_id", lambda: 1)
    monkeypatch.setattr(ego, "run_js", lambda body, payload, **kw: seen.update(payload) or {"status": "attached"})
    run = ego.provider_runner()
    run("body", {"file_transfer": {"transport": "aside-session-path/v1", "source_path": str(video),
                                   "size": 11, "sha256": digest}}, cwd=tmp_path, timeout=5)
    assert "file_transfer" not in seen
    assert seen["file"]["sha256"] == digest
    staged = tmp_path / seen["file"]["path"]
    assert staged.read_bytes() == b"video-bytes"
    with pytest.raises(ego.EgoRuntimeError):
        run("body", {"file_transfer": {"transport": "aside-session-path/v1", "source_path": str(video),
                                       "size": 11, "sha256": "0" * 64}}, cwd=tmp_path, timeout=5)
