"""Persist an already attached upload's metadata without reuploading it.

YouTube Studio sometimes reports the attached video's metadata as ready before
its "변경사항이 저장됨" notice can be observed, so the publisher stops with an
uncertain acknowledgement. Reuploading would create a second video, so the only
safe move is to re-run the same metadata save against the ID already in the
attachment receipt, and require a fresh page read to confirm the exact title and
description before the publisher is resumed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import youtube_shorts_publisher as publisher
from youtube_shorts_aside_adapter import (
    SAVE_ATTACHED_METADATA_JS,
    AsideHeadlessU0Provider,
    _default_aside_runner,
)


def recover(root: str | Path, *, manifest_name: str = "07_provider_manifest.json") -> dict:
    root = Path(root).expanduser().resolve()
    receipt_path = root / "provider/attachment_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "attached" or not receipt.get("provider_id"):
        raise RuntimeError("첨부 영수증에 확정된 provider_id가 없습니다. 재업로드하지 마세요.")

    manifest = publisher.load_manifest(root / manifest_name)
    sentinel = receipt["draft_sentinel"]
    if manifest.draft_sentinel != sentinel:
        raise RuntimeError("첨부 영수증의 draft sentinel이 현재 manifest와 다릅니다.")
    video_digest = publisher.sha256_file(manifest.video) if hasattr(publisher, "sha256_file") else None
    if video_digest and receipt.get("attachment_sha256") != video_digest:
        raise RuntimeError("첨부 영수증의 영상 해시가 현재 final.mp4와 다릅니다.")

    port = AsideHeadlessU0Provider()
    port._runner = _default_aside_runner()
    result = port._run(
        SAVE_ATTACHED_METADATA_JS,
        {
            "id": receipt["provider_id"],
            "channel": manifest.expected_channel,
            "title": manifest.title,
            "description": manifest.description,
            "sentinel": sentinel,
        },
        cwd=manifest.video.parent,
        timeout=150,
    )
    if result.get("provider_id") != receipt["provider_id"]:
        raise RuntimeError("복구 대상 provider_id가 첨부 영수증과 다릅니다.")
    if result.get("status") != "pass" or result.get("fresh_read_verified") is not True:
        raise RuntimeError(
            "메타데이터 저장을 새 페이지 조회로 확인하지 못했습니다: "
            + str(result.get("error") or result.get("status"))
        )

    existing = sorted((root / "provider").glob("metadata-recovery-*.json"))
    out = root / "provider" / f"metadata-recovery-{len(existing) + 1:02d}.json"
    out.write_text(json.dumps(dict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"receipt": str(out), "provider_id": receipt["provider_id"], **dict(result)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="candidate root holding provider/attachment_receipt.json")
    args = parser.parse_args(argv)
    result = recover(args.root)
    print(f"메타데이터 저장 확인: {result['provider_id']} / 추가 클릭 {result.get('save_clicks')}회")
    print(f"증거: {result['receipt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
