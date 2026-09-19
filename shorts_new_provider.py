"""Bind a reviewed new-source candidate to a provider upload manifest.

The replacement path pins the exact slot of the video being retired. A first
publish has no such slot, so the manifest carries no predecessor and the
publisher's append-only planner picks the next free 11:00/20:00 KST slot after
everything already reserved.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import youtube_shorts_publisher as publisher
from content_production_policy import load_channel_policy, shorts_description
from shorts_repair_prepare import binding

EXPECTED_CHANNEL = "나민수 AI"


def prepare(root: str | Path) -> dict:
    root = Path(root).expanduser().resolve()
    target = root / "07_provider_manifest.json"
    if target.exists():
        raise RuntimeError("공급자 매니페스트가 이미 있습니다. 새로 만들지 말고 이어서 진행하세요.")
    production = json.loads((root / "production_manifest.json").read_text(encoding="utf-8"))
    source_key = production["source_id"]
    if production.get("replacement_for_provider_id"):
        raise RuntimeError("교체 대상이 있는 후보입니다. 교체 경로를 쓰세요.")

    script = (root / "07_script_final.txt").read_text(encoding="utf-8").strip()
    urls = [f"https://youtu.be/{source_key}", f"https://www.youtube.com/watch?v={source_key}"]
    video = binding(root / "final.mp4")
    # 설명글의 채널 목적·CTA와 관련 동영상은 채널 정책 정본에서 온다.
    channel_policy = load_channel_policy()
    manifest = {
        "source_key": source_key,
        "title": production["render_inputs"]["upload_title"],
        "description": shorts_description(script, urls, channel_policy),
        "final_mp4": str(root / "final.mp4"),
        "final_mp4_sha256": video["sha256"],
        "original_urls": urls,
        "expected_channel": EXPECTED_CHANNEL,
        "related_video_id": channel_policy["introduction_video_id"],
        "journal": str(root / "provider/journal.json"),
    }
    visual = json.loads((root / "visual_validation.json").read_text(encoding="utf-8"))
    if visual.get("status") != "pass" or visual.get("video_sha256") != video["sha256"]:
        raise RuntimeError("렌더된 영상에 대한 실제 시각검사가 통과해야 합니다.")

    production["content_lineage"]["video"] = video
    production["content_lineage"]["visual_validation"] = binding(root / "visual_validation.json")
    (root / "production_manifest.json").write_text(
        json.dumps(production, ensure_ascii=False, indent=2), encoding="utf-8")
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    publisher.validate_local_candidate(publisher.load_manifest(target))
    return {"status": "provider_manifest_validated", "source_key": source_key,
            "manifest": str(target), "title": manifest["title"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.root), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
