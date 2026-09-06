from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import cafe_manifest_publisher as publisher


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_bundle(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(publisher, "PROJECT", tmp_path)
    cafe = tmp_path / "outputs/sample-20260903/cafe"
    provider = cafe.parent / "provider"
    cafe.mkdir(parents=True)
    manifest_path = cafe / "06_cafe_manifest.json"
    manifest = {
        "source_key": "sampleKey01",
        "source_url": "https://youtu.be/sampleKey01",
        "source_long_url": "https://www.youtube.com/watch?v=sampleKey01",
        "title": "검증용 카페 제목",
        "body_file": "body.txt",
        "category": publisher.EXPECTED_CATEGORY,
        "expected_quotes": 5,
        "expected_quote_texts": [f"소제목 {index}" for index in range(1, 6)],
        "expected_images": 5,
        "images": [f"images/{index}.jpg" for index in range(1, 6)],
        "provider_editor_opened": False,
        "provider_mutation": False,
        "notebooklm_answer": "notebooklm/notebooklm-answer.md",
        "tail": {
            "cta_text": publisher.EXPECTED_CTA_TEXT,
            "family_day_url": publisher.EXPECTED_CTA_URL,
            "source_label": publisher.EXPECTED_SOURCE_LABEL,
            "source_url": "https://youtu.be/sampleKey01",
            "source_long_url": "https://www.youtube.com/watch?v=sampleKey01",
        },
    }
    write_json(manifest_path, manifest)
    (cafe / "notebooklm").mkdir(parents=True)
    (cafe / "notebooklm/notebooklm-answer.md").write_text("검증된 NotebookLM 카페 원고", encoding="utf-8")
    write_json(
        cafe / "notebooklm/notebooklm-provider-evidence.json",
        {
            "status": "pass",
            "account": "u0",
            "notebookTitle": "민수대표님_카페글",
            "sourceUrl": "https://www.youtube.com/watch?v=sampleKey01",
        },
    )
    (cafe / "body.txt").write_text("검증된 NotebookLM 카페 원고", encoding="utf-8")
    local_path = cafe / "11_local_validation.json"
    write_json(local_path, {"status": "pass", "source_key": "sampleKey01"})
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    local_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()
    relative_manifest = str(manifest_path.relative_to(tmp_path))
    write_json(
        provider / "07_approval_validation.json",
        {
            "status": "pass",
            "sourceKey": "sampleKey01",
            "sourceOfTruth": relative_manifest,
            "manifestSha256": manifest_hash,
            "failures": [],
        },
    )
    write_json(
        provider / "08_launch_consistency_gate.json",
        {
            "status": "pass",
            "manifestSha256": manifest_hash,
            "summary": {"issues": 0},
        },
    )
    write_json(
        provider / "09_cafe_only_local_gate.json",
        {
            "status": "pass",
            "scope": "cafe_only",
            "manifestSha256": manifest_hash,
            "localValidationSha256": local_hash,
        },
    )
    return manifest_path


def test_cafe_only_gate_is_self_contained_and_ignores_cross_channel_state(tmp_path, monkeypatch):
    manifest_path = make_bundle(tmp_path, monkeypatch)
    _, _, provider, evidence = publisher.resolve_manifest(str(manifest_path))
    result = publisher.validate_cafe_eligibility(manifest_path, provider, evidence)

    assert result["status"] == "pass"
    assert result["scope"] == "cafe_only"
    assert result["failures"] == []
    assert result["crossChannelMutationAllowed"] == {"shorts": False, "community": False}
    assert all(result["checks"].values())


def test_validate_only_has_no_provider_or_crm_side_effect(tmp_path, monkeypatch, capsys):
    manifest_path = make_bundle(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.argv", ["cafe_manifest_publisher.py", "--manifest", str(manifest_path), "--validate-only"])

    with pytest.raises(SystemExit) as stopped:
        publisher.main()
    result = json.loads(capsys.readouterr().out)

    assert stopped.value.code == 0
    assert result["status"] == "pass"
    assert not (manifest_path.parent / "provider/13_provider_evidence.json").exists()
    assert not (manifest_path.parent / "provider/14_crm_evidence.json").exists()


def test_missing_notebooklm_answer_and_provider_evidence_fail_closed(tmp_path, monkeypatch):
    manifest_path = make_bundle(tmp_path, monkeypatch)
    (manifest_path.parent / "notebooklm/notebooklm-answer.md").unlink()
    (manifest_path.parent / "notebooklm/notebooklm-provider-evidence.json").unlink()
    _, _, provider, evidence = publisher.resolve_manifest(str(manifest_path))

    result = publisher.validate_cafe_eligibility(manifest_path, provider, evidence)

    assert result["status"] == "fail"
    assert "notebooklm_answer_present" in result["failures"]
    assert "notebooklm_provider_evidence_exact" in result["failures"]


def test_provider_verification_and_crm_order_remain_fail_closed():
    source = Path(publisher.__file__).read_text(encoding="utf-8")

    assert "state.titleExact&&state.categoryExact&&state.ctaExact" in source
    assert "state.sourceRaw&&state.sourceLongRaw&&state.images===5" in source
    assert source.index('if verified.get("status") != "verified"') < source.index("crm = crm_emit(source_key, evidence_path)")
    assert source.index('evidence_path.write_text(json.dumps(evidence_payload') < source.index("crm = crm_emit(source_key, evidence_path)")
    assert '"sourceUrls": [short_url, long_url]' in source


def test_canonical_cafe_article_url_decodes_legacy_iframe_redirect():
    raw = (
        "https://cafe.naver.com/westudyssat?iframe_url_utf8="
        "%2FArticleRead.nhn%253Fclubid%3D26321967%2526articleid%3D6081%2526menuid%3D163"
    )
    assert publisher.canonical_cafe_article_url(raw) == (
        "https://cafe.naver.com/westudyssat/6081",
        "6081",
    )


def test_canonical_cafe_article_url_rejects_board_root():
    with pytest.raises(RuntimeError, match="no article id"):
        publisher.canonical_cafe_article_url("https://cafe.naver.com/westudyssat")


def test_manifest_long_source_url_prefers_canonical_field_and_keeps_legacy_alias():
    canonical = "https://www.youtube.com/watch?v=canonical1"
    legacy = "https://www.youtube.com/watch?v=legacy00001"
    assert publisher.manifest_long_source_url({"source_url_long": canonical, "source_long_url": legacy}) == canonical
    assert publisher.manifest_long_source_url({"source_long_url": legacy}) == legacy


def test_publish_window_uses_queue_as_source_of_truth(tmp_path, monkeypatch):
    monkeypatch.setattr(publisher, "PROJECT", tmp_path)
    queue_path = tmp_path / publisher.QUEUE_POLICY_PATH
    evidence_path = tmp_path / "outputs/already/cafe/provider/13_provider_evidence.json"
    write_json(queue_path, {
        "timezone": "Asia/Seoul",
        "minimum_gap_hours": 5,
        "maximum_successes_per_day": 2,
        "entries": [{
            "source_key": "already",
            "provider_evidence": str(evidence_path.relative_to(tmp_path)),
        }],
    })
    write_json(evidence_path, {
        "status": "published_verified",
        "verifiedAt": "2026-09-04T10:00:00+09:00",
    })
    with pytest.raises(RuntimeError, match="gap has not reached 5 hours"):
        publisher.enforce_cafe_publish_window(
            publisher.datetime.fromisoformat("2026-09-04T14:59:59+09:00"))
    publisher.enforce_cafe_publish_window(
        publisher.datetime.fromisoformat("2026-09-04T15:00:00+09:00"))


def test_publish_window_counts_locked_success_reservations(tmp_path, monkeypatch):
    monkeypatch.setattr(publisher, "PROJECT", tmp_path)
    queue_path = tmp_path / publisher.QUEUE_POLICY_PATH
    entries = []
    for index, hour in enumerate((10, 15), start=1):
        evidence = tmp_path / f"outputs/item{index}/cafe/provider/13_provider_evidence.json"
        entries.append({
            "source_key": f"item{index}",
            "provider_evidence": str(evidence.relative_to(tmp_path)),
        })
        write_json(evidence.with_name("12_provider_success_reservation.json"), {
            "status": "provider_success_reserved",
            "verifiedAt": f"2026-09-04T{hour:02d}:00:00+09:00",
        })
    write_json(queue_path, {
        "timezone": "Asia/Seoul",
        "minimum_gap_hours": 5,
        "maximum_successes_per_day": 2,
        "entries": entries,
    })
    with pytest.raises(RuntimeError, match="daily publish cap reached: 2/2"):
        publisher.enforce_cafe_publish_window(
            publisher.datetime.fromisoformat("2026-09-04T23:00:00+09:00"))
