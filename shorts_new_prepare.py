"""Prepare a Shorts candidate for a source that has no scheduled predecessor.

The repair path binds every candidate to the exact video it replaces. A link the
owner submits for the first time has nothing to replace, so this builds the same
production manifest through the same gates, minus the predecessor binding, and
leaves scheduling to the publisher.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import notebooklm_shorts as scripts
from shorts_repair_prepare import PROJECT, binding

PRESENTER_SOURCE = (
    PROJECT
    / "outputs/pw8Bt97U6fk-20260902/repair-20260906/shorts-v18-continuous-v1/production_manifest.json"
)


def _source_root(source_key: str) -> Path:
    roots = [p for p in (PROJECT / "outputs").glob(source_key + "-*") if p.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(
            f"원본 {source_key}의 공동 산출물 루트가 정확히 1개여야 합니다 (현재 {len(roots)}개)."
        )
    return roots[0]


def _download_source(source_key: str, root: Path) -> tuple[Path, str]:
    """Fetch the original video once, and take the credit line from its channel."""
    video = root / "source_original.mp4"
    evidence = root / "source_download_evidence.json"
    if video.exists() and evidence.is_file():
        return video, json.loads(evidence.read_text(encoding="utf-8"))["source_credit"]
    import yt_dlp

    options = {
        "format": "18", "outtmpl": str(video), "noplaylist": True,
        "quiet": True, "no_warnings": False, "js_runtimes": {"node": {}},
        "extractor_args": {"youtube": {"player_client": ["mweb"]}},
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(f"https://youtu.be/{source_key}", download=True)
    if info.get("id") != source_key:
        raise RuntimeError("내려받은 영상의 ID가 대상과 다릅니다.")
    credit = "출처: " + info["channel"]
    evidence.write_text(json.dumps({
        "source_key": source_key, "channel": info["channel"], "title": info["title"],
        "duration_seconds": info["duration"], "source": binding(video),
        "backend": "project yt-dlp mweb format 18; no cookies",
        "source_credit": credit,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return video, credit


def prepare(source_key: str) -> dict:
    source_root = _source_root(source_key)
    root = source_root / "shorts"
    if (root / "final.mp4").exists():
        raise RuntimeError("이미 렌더된 후보가 있습니다. 새로 만들지 말고 그것을 검토·복구하세요.")
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, str(PROJECT / "content_workflow_preflight.py"), "--runtime", "--json"],
        stdout=subprocess.DEVNULL, check=True,
    )

    source_video, credit = _download_source(source_key, root)
    if not credit.startswith("출처: ") or len(credit) <= 4:
        raise RuntimeError("원본 출처 표기가 없습니다.")

    script_path = root / "07_script_final.txt"
    if not script_path.exists():
        subprocess.run([
            sys.executable, str(PROJECT / "notebooklm_shorts.py"),
            "--url", f"https://youtu.be/{source_key}", "--out", str(script_path),
            "--headline-out", str(root / "06_headcopy_candidates.txt"),
            "--evidence-dir", str(root / "notebooklm"),
            "--preserve-authorized-wording",
        ], check=True)

    script = script_path.read_text(encoding="utf-8").strip()
    scripts.validate_intro_promise(script)
    match = re.search(r"(?m)^(\d+)분 짜리 영상 내용을 모두 정리했습니다\.", script)
    if not match:
        raise RuntimeError("고정 CTA가 없습니다.")
    measured = scripts.duration_minutes(
        scripts.get_video_duration(f"https://youtu.be/{source_key}")
    )
    if int(match[1]) != measured:
        raise RuntimeError("CTA 분 수가 실측 원본 길이와 다릅니다.")

    import shorts_v7_builder as builder

    builder.SOURCE_MINUTES = int(match[1])
    sections = builder.split_seven_sections(script)

    answer_path = root / "notebooklm/notebooklm-answer-recovered.md"
    recovery_path = root / "notebooklm/notebooklm-answer-recovery-evidence.json"
    if answer_path.exists() != recovery_path.exists():
        raise RuntimeError("복구한 원응답과 그 증거는 함께 있어야 합니다.")
    if not answer_path.exists():
        answer_path = root / "notebooklm/notebooklm-answer.md"
    fact_path = root / "notebooklm" / scripts.FACT_VERIFICATION_FILENAME

    heads = scripts.extract_head_copy_candidates(answer_path.read_text(encoding="utf-8"))
    title = " ".join(scripts.head_copy_lines(heads[0]))
    presenter_binding = json.loads(PRESENTER_SOURCE.read_text(encoding="utf-8"))["render_inputs"]["presenter"]
    answer = binding(answer_path)
    manifest = {
        "source_id": source_key, "content_rewrite_applied": False,
        "provider_mutation_attempted": False, "studio_opened": False, "crm_emitted": False,
        "content_lineage": {
            "mode": "notebooklm_verbatim", "answer": answer,
            "provider_evidence": binding(root / "notebooklm/notebooklm-provider-evidence.json"),
            **({"answer_recovery": binding(recovery_path),
                "stored_answer": binding(root / "notebooklm/notebooklm-answer.md")}
               if recovery_path.exists() else {}),
            "script": binding(script_path), "source_minutes": int(match[1]),
            **({"fact_verifications": binding(fact_path)} if fact_path.exists() else {}),
            "wording_authorization": {
                "scope": "preserve_original_absolute_wording", "source_key": source_key,
                "answer_sha256": answer["sha256"],
                "instruction": "쇼츠는 과장 표현 상관없이 진행한다. 원응답대로 하면된다."}},
        "render_inputs": {
            "source": binding(source_video), "presenter": presenter_binding,
            "headcopy": binding(root / "06_headcopy_candidates.txt"),
            "source_credit": credit, "upload_title": title,
            "scene_jobs": [section.split(".")[0] for section in sections[:6]],
            "scene_sentinels": [[section.split(".")[0]] for section in sections[1:6]]},
    }
    (root / "production_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    from content_lineage import validate_shorts_origin

    validate_shorts_origin(root)
    return {"status": "prepared", "source_key": source_key, "root": str(root),
            "source_minutes": int(match[1]), "upload_title": title,
            "next": "render, inspect, then publish on a free slot"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_key")
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.source_key), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
