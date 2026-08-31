from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path


PROJECT = Path(__file__).resolve().parent
SCRIPT = PROJECT / "outputs/1CLc-VeEivk-20260829/provider/11_publish_cafe.py"
PROVIDER_EVIDENCE = PROJECT / "outputs/1CLc-VeEivk-20260829/cafe/provider/13_provider_evidence.json"
CRM_EVIDENCE = PROJECT / "outputs/1CLc-VeEivk-20260829/cafe/provider/14_crm_evidence.json"


def load_publisher_module():
    spec = importlib.util.spec_from_file_location("publish_1clc_cafe", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cafe_only_gate_excludes_only_expired_cross_channel_schedule() -> None:
    module = load_publisher_module()
    result = module.validate_cafe_eligibility()

    assert result["status"] == "pass"
    assert result["scope"] == "cafe_only"
    assert result["failures"] == []
    assert result["excludedNonCafeGateFailures"] == ["selected_schedules_future"]
    assert result["unexpectedGlobalFailures"] == []
    assert all(result["checks"].values())
    assert result["runtimeRequirements"]["duplicate_zero_by"] == [
        "exact_title",
        "source_key",
        "short_source_url",
        "long_source_url",
    ]


def test_validate_only_has_no_provider_or_crm_side_effect() -> None:
    assert not PROVIDER_EVIDENCE.exists()
    assert not CRM_EVIDENCE.exists()

    completed = subprocess.run(
        [str(PROJECT / ".venv312/bin/python"), str(SCRIPT), "--validate-only"],
        cwd=PROJECT,
        text=True,
        capture_output=True,
        check=False,
    )
    result = json.loads(completed.stdout)

    assert completed.returncode == 0, completed.stderr
    assert result["status"] == "pass"
    assert not PROVIDER_EVIDENCE.exists()
    assert not CRM_EVIDENCE.exists()


def test_provider_verification_and_crm_order_remain_fail_closed() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "state.titleExact&&state.categoryExact&&state.ctaExact" in source
    assert "state.oglinks>=1&&state.embeds>=1?'verified':'observed'" in source
    assert source.index('if verified.get("status") != "verified"') < source.index("crm = crm_emit(evidence_path)")
    assert source.index('evidence_path.write_text(json.dumps(evidence') < source.index("crm = crm_emit(evidence_path)")
    assert '"sourceUrls": [EXPECTED_SOURCE_URL, EXPECTED_LONG_SOURCE_URL]' in source
