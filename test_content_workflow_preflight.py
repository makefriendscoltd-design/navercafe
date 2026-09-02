from pathlib import Path

import content_workflow_preflight as preflight


def test_static_content_workflow_preflight_passes_from_tracked_sources():
    result = preflight.audit(Path(__file__).resolve().parent, runtime=False)

    assert result["status"] == "pass", result["failures"]
    assert result["checks"]["codex_only_no_claude_cli_invocation"] is True
    assert result["checks"]["legacy_entrypoint_fail_closed"] is True
    assert result["checks"]["legacy_entrypoint_has_no_publish_policy"] is True
    assert result["checks"]["daily_schedule_contract"] is True
