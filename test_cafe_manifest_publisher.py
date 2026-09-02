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
        "title": "검증용 카페 제목",
        "category": publisher.EXPECTED_CATEGORY,
        "expected_quotes": 5,
        "expected_quote_texts": [f"소제목 {index}" for index in range(1, 6)],
        "expected_images": 5,
        "images": [f"images/{index}.jpg" for index in range(1, 6)],
        "provider_editor_opened": False,
        "provider_mutation": False,
        "tail": {
            "cta_text": publisher.EXPECTED_CTA_TEXT,
            "family_day_url": publisher.EXPECTED_CTA_URL,
            "source_label": publisher.EXPECTED_SOURCE_LABEL,
            "source_url": "https://youtu.be/sampleKey01",
        },
    }
    write_json(manifest_path, manifest)
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


def test_provider_verification_and_crm_order_remain_fail_closed():
    source = Path(publisher.__file__).read_text(encoding="utf-8")

    assert "state.titleExact&&state.categoryExact&&state.ctaExact" in source
    assert "state.oglinks>=1&&state.embeds>=1?'verified':'observed'" in source
    assert source.index('if verified.get("status") != "verified"') < source.index("crm = crm_emit(source_key, evidence_path)")
    assert source.index('evidence_path.write_text(json.dumps(evidence_payload') < source.index("crm = crm_emit(source_key, evidence_path)")
    assert '"sourceUrls": [short_url, long_url]' in source
