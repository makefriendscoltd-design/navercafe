"""Publish one provider-saved Naver Cafe draft at its scheduled time.

The caller is expected to be a one-shot Orca automation.  This command is
fail-closed: it publishes only after the exact saved draft passes every
structure check encoded in the manifest.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aside_browser import AsideError, publish_saved_naver_cafe_draft
from external_publish_tracking import track_external_event
from telegram_publish_approval import (
    TelegramApproval,
    TelegramApprovalError,
    load_approval_credentials,
)


PROJECT_DIR = Path(__file__).resolve().parent
MIGRATED_CONFIG = Path.home() / "orca" / "projects" / "ccidacafe" / "config.ini"
PROVIDER_LOCK = Path("/tmp/aimax-naver-provider.lock")


@contextmanager
def _provider_lock(timeout_seconds: int = 900):
    """Serialize Naver provider UI mutations across Cafe workers."""
    deadline = time.monotonic() + timeout_seconds
    acquired = False
    try:
        while not acquired:
            try:
                PROVIDER_LOCK.mkdir()
                acquired = True
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("네이버 제공자 잠금을 15분 안에 획득하지 못했습니다.")
                time.sleep(5)
        yield
    finally:
        if acquired:
            try:
                PROVIDER_LOCK.rmdir()
            except FileNotFoundError:
                pass


def _load_config() -> configparser.RawConfigParser:
    configured = os.environ.get("NAVERCAFE_CONFIG", "").strip()
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.extend([PROJECT_DIR / "config.ini", MIGRATED_CONFIG])
    path = next((item for item in candidates if item.is_file()), None)
    if path is None:
        raise RuntimeError("네이버 카페/CCIDA 설정 파일을 찾지 못했습니다.")
    config = configparser.RawConfigParser()
    config.read(path, encoding="utf-8")
    return config


def _load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "title", "cafe_url", "board_name", "expected_images",
        "expected_quotes", "expected_sequence", "expected_quote_texts",
        "cta_link_url", "source_url", "source_key",
    }
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"예약 발행 manifest 필드가 없습니다: {', '.join(missing)}")
    return data


def _write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _notify_failure(config: configparser.RawConfigParser, *, title: str, reason: str) -> bool:
    """Notify only the verified CCIDA private destination; never a general room."""
    try:
        credentials = load_approval_credentials(config)
        client = TelegramApproval(
            str(credentials["token"]), str(credentials["chat_id"])
        )
        client.verify_destination(
            expected_bot=str(credentials["expected_bot"]),
            expected_chat_type="private",
        )
        count = client.send_text(
            "[네이버 카페 예약 발행 중단]\n"
            f"제목: {title}\n"
            f"사유: {reason}\n"
            "검증에 실패해 등록 버튼은 누르지 않았습니다."
        )
        track_external_event(
            "telegram",
            f"naver-cafe-scheduler-alert:{title}",
            campaign="youtube-content-repurpose-cafe-scheduler-alert",
            stage="sent",
            count=count,
        )
        return True
    except (TelegramApprovalError, OSError, RuntimeError):
        return False


def run_manifest(manifest_path: Path, result_path: Path) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    config = _load_config()
    account = str(manifest.get("aside_account") or "").strip() or config.get(
        "BROWSER", "aside_account", fallback=""
    ).strip() or None
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        with _provider_lock():
            provider = publish_saved_naver_cafe_draft(
                str(manifest["title"]),
                cafe_url=str(manifest["cafe_url"]),
                board_name=str(manifest["board_name"]),
                expected_images=int(manifest["expected_images"]),
                expected_quotes=int(manifest["expected_quotes"]),
                expected_sequence=list(manifest["expected_sequence"]),
                expected_quote_texts=list(manifest["expected_quote_texts"]),
                cta_link_url=str(manifest["cta_link_url"]),
                source_url=str(manifest["source_url"]),
                account=account,
                preview_path=manifest.get("provider_preview_path") or None,
            )
        if provider.get("status") != "published" or not provider.get("url"):
            raise AsideError("네이버 제공자의 발행 완료 URL을 확인하지 못했습니다.")
        tracked = track_external_event(
            "naver_cafe",
            str(manifest["source_key"]),
            campaign="youtube-content-repurpose",
            stage="sent",
            count=1,
        )
        result = {
            "status": "published",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "crm_tracked": tracked,
        }
    except Exception as exc:
        reason = str(exc).strip() or type(exc).__name__
        notified = _notify_failure(
            config, title=str(manifest.get("title") or "제목 확인 불가"), reason=reason
        )
        result = {
            "status": "blocked",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "ccida_private_notified": notified,
        }
    _write_result(result_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="네이버 카페 임시글 1회 예약 발행")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args()
    result = run_manifest(args.manifest.expanduser(), args.result.expanduser())
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "published" else 1


if __name__ == "__main__":
    sys.exit(main())
