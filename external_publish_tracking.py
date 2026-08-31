"""Record provider-confirmed external sends without content or URLs."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


TRACKER = Path("/Users/apple/orca/projects/aimax-crm-observability/bin/aimax-crm-track")
VALID_STAGES = {
    "agent_send_tool_failed", "agent_send_tool_succeeded", "canceled",
    "checkout_started", "delivered", "internal_cta_clicked", "landing_viewed",
    "opened", "outbound_clicked", "paid", "planned", "refunded",
    "send_attempted", "sent", "session_ended", "session_started",
}


def track_external_event(
    channel: str,
    source_key: str,
    *,
    campaign: str = "youtube-content-repurpose",
    stage: str = "sent",
    count: int = 1,
) -> bool:
    if stage not in VALID_STAGES:
        print("[추적 주의] 공통 장부가 지원하지 않는 단계라 기록하지 못했습니다.")
        return False
    if not TRACKER.is_file():
        print("[추적 주의] 외부 발행 장부 실행 파일이 없어 기록하지 못했습니다.")
        return False
    digest = hashlib.sha256(
        f"{channel}:{campaign}:{stage}:{source_key}".encode("utf-8")
    ).hexdigest()[:20]
    command = [
        str(TRACKER),
        "emit",
        "--source-system", "codex",
        "--channel", channel,
        "--stage", stage,
        "--status", "success",
        "--origin-agent", "codex",
        "--project", "navercafe",
        "--campaign", campaign,
        "--count", str(max(0, int(count))),
        "--dedupe-key", f"external-{digest}",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print("[추적 주의] 게시 성공은 확인했지만 공통 장부 기록은 실패했습니다.")
        return False
    return True


def track_publication(channel: str, source_key: str) -> bool:
    return track_external_event(channel, source_key)
