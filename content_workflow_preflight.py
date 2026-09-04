#!/usr/bin/env python3
"""Read-only readiness check for the three-channel content workflow."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import content_production_policy as policy


PROJECT = Path(__file__).resolve().parent
REQUIRED_TRACKED_FILES = (
    "AGENTS.md",
    "SHORTS_SPEC.md",
    "content_production_policy.py",
    "content_factcheck.py",
    "notebooklm_source.py",
    "notebooklm_aside.py",
    "notebooklm_shorts.py",
    "notebook_cafe_auto.py",
    "shorts_video.py",
    "youtube_cardnews_pipeline.py",
    "cafe_manifest_publisher.py",
    "naver_cafe_fresh_publish_fallback.py",
    "run_content_link.command",
    "test_fixtures/notebooklm_shorts/v12_bad_dcl.md",
    "test_fixtures/notebooklm_shorts/v13_compliant.md",
    "test_fixtures/notebooklm_shorts/v13_failed_dcl_provider.md",
    "outputs/7cimtg6LPHg-20260902/shorts/build_v7_target.py",
    "outputs/uX6zwf4b8sM-20260829/shorts/renderer/aimax_video_pipeline.py",
    "outputs/uX6zwf4b8sM-20260829/shorts/renderer/assets/bgm/DSGNBass-Millitary_Action_Tri-Elevenlabs.mp3",
    "outputs/uX6zwf4b8sM-20260829/shorts/renderer/assets/sfx/WHSH-Whoosh_Short_Clean-Elevenlabs.mp3",
    "outputs/uX6zwf4b8sM-20260829/shorts/renderer/assets/fonts/BMHANNA_11yrs_ttf.ttf",
    "outputs/uX6zwf4b8sM-20260829/shorts/renderer/assets/fonts/Cafe24Ohsquare.ttf",
    "outputs/20260822-shorts-correction-audit/rebaseline/remade-v7-reference-restored/TZO3_2Krsqk/render_config.json",
)
RUNTIME_SOURCE_FILES = (
    "notebooklm_aside.py",
    "notebooklm_source.py",
    "notebook_cafe_auto.py",
    "youtube_cardnews_pipeline.py",
    "run_content_link.command",
)


def _tracked(project: Path, relative: str) -> bool:
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=project,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def audit(project: Path = PROJECT, *, runtime: bool = False) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}
    for relative in REQUIRED_TRACKED_FILES:
        path = project / relative
        checks[f"file:{relative}"] = path.is_file()
        checks[f"tracked:{relative}"] = _tracked(project, relative)

    source_text = "\n".join(
        (project / relative).read_text(encoding="utf-8")
        for relative in RUNTIME_SOURCE_FILES
        if (project / relative).is_file()
    ).lower()
    forbidden_invocations = (
        "shutil.which(\"claude\")",
        "shutil.which('claude')",
        "[\"claude\", \"-p\"]",
        "['claude', '-p']",
        "claude -p",
    )
    checks["codex_only_no_claude_cli_invocation"] = not any(
        marker in source_text for marker in forbidden_invocations
    )
    forbidden_browser_backends = (
        "notebooklm-py",
        "browser-cookies",
        "sync_playwright",
        "selenium.webdriver",
    )
    notebook_runtime = "\n".join(
        (project / relative).read_text(encoding="utf-8").lower()
        for relative in ("notebooklm_aside.py", "notebooklm_source.py")
        if (project / relative).is_file()
    )
    checks["notebooklm_aside_only_no_cookie_or_browser_fallback"] = not any(
        marker in notebook_runtime for marker in forbidden_browser_backends
    )
    entrypoint = (project / "run_content_link.command").read_text(encoding="utf-8")
    agent_rules = (project / "AGENTS.md").read_text(encoding="utf-8")
    checks["legacy_entrypoint_fail_closed"] = (
        "content_workflow_preflight.py --runtime --json" in entrypoint
        and "exit 2" in entrypoint
        and "youtube_cardnews_pipeline.py" not in entrypoint
    )
    checks["legacy_entrypoint_has_no_publish_policy"] = "--publish-policy" not in entrypoint
    checks["source_key_output_isolation_contract"] = (
        "outputs/<source_key>-<YYYYMMDD>/" in agent_rules
        and "dirty/stale 작업 폴더" in agent_rules
        and "현재 `main` HEAD" in agent_rules
    )
    checks["aside_account_u0"] = policy.ASIDE_ACCOUNT == "u0"
    checks["cardnews_exactly_ten_square"] = policy.CARDNEWS == {
        "width": 1080,
        "height": 1080,
        "aspect_ratio": "1:1",
        "count": 10,
    }
    checks["cardnews_editorial_contract"] = (
        policy.CARDNEWS_EDITORIAL.get("content_count") == 8
        and policy.CARDNEWS_EDITORIAL.get("max_warning_first_cards") == 2
        and policy.CARDNEWS_EDITORIAL.get("closing_cta1") == "댓글 AIMAX"
        and policy.CARDNEWS_EDITORIAL.get("closing_cta2") == "관련 정보 받기"
    )
    checks["shorts_video_contract"] = policy.VIDEO == {
        "width": 1080,
        "height": 1920,
        "fps": 30,
    }
    checks["daily_schedule_contract"] = (
        policy.SCHEDULE.get("max_per_day") == 2
        and policy.SCHEDULE.get("minimum_gap_hours") == 5
        and policy.SCHEDULE.get("include_weekends") is True
    )
    checks["notebook_bindings"] = bool(
        policy.validate_notebook_binding(
            "cafe",
            account="u0",
            title=policy.CAFE_NOTEBOOK["title"],
            notebook_id=policy.CAFE_NOTEBOOK["id"],
        )
        and policy.validate_notebook_binding(
            "shorts",
            account="u0",
            title=policy.SHORTS_NOTEBOOK["title"],
            notebook_id=policy.SHORTS_NOTEBOOK["id"],
        )
    )
    checks["shorts_notebook_instruction_contract"] = (
        policy.SHORTS_NOTEBOOK_PROMPT == "이 영상으로 숏폼 스크립트 만들어줘."
        and policy.SHORTS_NOTEBOOK_INSTRUCTION_VERSION == "v14.0"
        and policy.notebook_instruction_sha256(policy.SHORTS_NOTEBOOK_INSTRUCTION)
        == policy.SHORTS_NOTEBOOK_INSTRUCTION_SHA256
        and "### 헤드카피라이팅" in policy.SHORTS_NOTEBOOK_INSTRUCTION
        and all(
            marker in policy.SHORTS_NOTEBOOK_INSTRUCTION
            for marker in policy.SHORTS_NOTEBOOK_REQUIRED_MARKERS
        )
    )
    checks["shorts_headline_pixel_gate"] = (
        policy.HEADLINE["font_size"] == 90
        and policy.HEADLINE_SAFE_WIDTH_PX == 920
        and policy.HEADLINE_SAFE_PROXY_CHAR_LIMIT == 13
        and policy.SHORTS_TITLE_FONT_PATH.is_file()
    )
    shorts_runtime_source = (project / "notebooklm_shorts.py").read_text(encoding="utf-8")
    checks["shorts_attempt_ledger_fail_closed"] = (
        "shorts-notebook-attempt-ledger/v1" in shorts_runtime_source
        and "validate_shorts_notebook_retry" in shorts_runtime_source
        and "unknown_after_provider_start" in policy.SHORTS_ATTEMPT_BLOCKING_STATUSES
        and "substantive_failed" in policy.SHORTS_ATTEMPT_BLOCKING_STATUSES
    )
    checks["shorts_verbatim_hash_stages"] = all(
        marker in shorts_runtime_source
        for marker in (
            "provider_answer_sha256",
            "citation_stripped_answer_sha256",
            "parser_normalized_body_sha256",
            "adopted_body_sha256",
            "final_adopted_body_sha256",
        )
    )

    if runtime:
        from aside_browser import resolve_aside_cli
        from youtube_cardnews_pipeline import CARDNEWS_ROOT

        runtime_paths = {
            "python312": project / ".venv312/bin/python",
            "cardnews_prompt": CARDNEWS_ROOT / "tools/deck-prompt.md",
            "cardnews_server": CARDNEWS_ROOT / "tools/server.js",
            "shorts_v7_builder": project / "outputs/7cimtg6LPHg-20260902/shorts/build_v7_target.py",
            "shorts_v7_renderer": project / "outputs/uX6zwf4b8sM-20260829/shorts/renderer/aimax_video_pipeline.py",
            "shorts_title_font": project / "outputs/uX6zwf4b8sM-20260829/shorts/renderer/assets/fonts/BMHANNA_11yrs_ttf.ttf",
            "shorts_body_font": project / "outputs/uX6zwf4b8sM-20260829/shorts/renderer/assets/fonts/Cafe24Ohsquare.ttf",
        }
        for name, path in runtime_paths.items():
            checks[f"runtime:{name}"] = path.is_file()
            details[f"runtime:{name}"] = str(path)
        for executable in ("ffmpeg", "ffprobe", "node"):
            resolved = shutil.which(executable)
            checks[f"runtime:{executable}"] = bool(resolved)
            details[f"runtime:{executable}"] = resolved or ""
        try:
            aside = resolve_aside_cli()
        except Exception as exc:
            checks["runtime:aside_cli"] = False
            details["runtime:aside_cli_error"] = type(exc).__name__
        else:
            checks["runtime:aside_cli"] = bool(aside)
            details["runtime:aside_cli"] = aside
        asset_results = {}
        for name in policy.MINSOO_PRESENTER_ASSETS:
            try:
                asset_results[name] = policy.validate_presenter_asset(Path("/Users/apple/Downloads") / name)["sha256"]
            except Exception:
                asset_results[name] = ""
        checks["runtime:approved_presenter_assets"] = all(asset_results.values())
        details["runtime:approved_presenter_asset_count"] = sum(bool(value) for value in asset_results.values())

        queue_path = project / "outputs/cafe-publish-queue-20260823/queue.json"
        if queue_path.is_file():
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            checks["runtime:cafe_queue_policy"] = (
                queue.get("maximum_successes_per_day") == 2
                and queue.get("minimum_gap_hours") == 5
                and queue.get("include_weekends") is True
                and queue.get("maximum_attempts_per_run") == 1
            )
            details["runtime:cafe_queue_entries"] = len(queue.get("entries") or [])

    failures = [name for name, passed in checks.items() if not passed]
    return {
        "schemaVersion": "content-workflow-preflight/v1",
        "status": "pass" if not failures else "fail",
        "mode": "runtime" if runtime else "static",
        "checks": checks,
        "details": details,
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="3채널 콘텐츠 제작 읽기 전용 사전 점검")
    parser.add_argument("--runtime", action="store_true", help="Aside·렌더 도구·승인 촬영본까지 확인")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    args = parser.parse_args(argv)
    result = audit(runtime=args.runtime)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{result['status']}: {', '.join(result['failures']) or 'all checks passed'}")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
