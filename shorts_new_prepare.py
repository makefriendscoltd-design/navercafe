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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import content_production_policy as policy
import notebooklm_shorts as scripts
from shorts_repair_prepare import PROJECT, binding
from youtube_source_options import source_options

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
    if video.exists() != evidence.exists():
        raise RuntimeError("원본 영상과 다운로드 증거 중 하나만 있습니다. 기존 파일을 덮어쓰지 않습니다.")
    import yt_dlp

    # Format 18 is absent on an increasing number of videos. Prefer it when the
    # provider exposes it, then accept another real MP4 progressive stream or an
    # MP4 video/audio pair. yt-dlp/ffmpeg performs the container merge locally.
    root.mkdir(parents=True, exist_ok=True)
    # Preserve failed attempts for diagnosis while letting the next scheduled
    # attempt start cleanly. A stale .part file must not disable all future runs.
    staging = Path(tempfile.mkdtemp(prefix=".source-download-", dir=root))
    template = staging / "source.%(ext)s"
    options = {
        **source_options(),
        "format": "18/b[ext=mp4]/bv*[ext=mp4]+ba[ext=m4a]",
        "outtmpl": str(template), "noplaylist": True,
        "merge_output_format": "mp4",
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(f"https://youtu.be/{source_key}", download=True)
        if info.get("id") != source_key:
            raise RuntimeError("내려받은 영상의 ID가 대상과 다릅니다.")
        channel = str(info.get("channel") or info.get("uploader") or "").strip()
        if not channel:
            raise RuntimeError("원본 채널명을 확인하지 못했습니다.")
        candidates = [p for p in staging.iterdir() if p.is_file() and not p.name.endswith(".part")]
        if len(candidates) != 1:
            raise RuntimeError(f"다운로드 산출물이 정확히 1개여야 합니다 (현재 {len(candidates)}개).")
        downloaded = candidates[0]
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type", "-of", "default=nw=1:nk=1", str(downloaded)],
            capture_output=True, text=True, check=True,
        )
        if probe.stdout.strip() != "video":
            raise RuntimeError("다운로드 산출물에서 영상 스트림을 확인하지 못했습니다.")
        shutil.move(str(downloaded), video)
    finally:
        if staging.exists() and not any(staging.iterdir()):
            staging.rmdir()
    credit = "출처: " + channel
    evidence.write_text(json.dumps({
        "source_key": source_key, "channel": channel, "title": info["title"],
        "duration_seconds": info["duration"], "source": binding(video),
        "backend": "project yt-dlp default clients; preferred format 18 with MP4 fallback; no cookies",
        "selected_format_id": info.get("format_id"),
        "source_credit": credit,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return video, credit


def _headcopy_order(heads: list[str], script: str) -> list[str]:
    """화면 첫 줄이 나레이션 첫 문장과 겹치지 않는 후보를 앞으로 보낸다.

    헤드카피는 귀로 듣는 말을 눈으로 또 읽히는 자리가 아니다. 지침(v19)이 같은 문장을
    금지하지만 후보 세 개 중 하나는 여전히 도입 첫 문장을 그대로 쓰고 나오며, 지금까지
    그 첫 후보를 그대로 썼다. 겹치지 않는 후보가 하나라도 있으면 그것을 먼저 쓴다.
    """
    opening = re.sub(r"\s+", "", script.strip().split(".")[0])
    if not opening:
        return heads

    def duplicates(candidate: str) -> bool:
        first_line = scripts.head_copy_lines(candidate)[0]
        return re.sub(r"\s+", "", first_line).rstrip("!?.") == opening.rstrip("!?.")

    fresh = [h for h in heads if not duplicates(h)]
    return fresh + [h for h in heads if duplicates(h)] if fresh else heads


def prepare(source_key: str) -> dict:
    source_root = _source_root(source_key)
    root = source_root / "shorts"
    if (root / "final.mp4").exists():
        raise RuntimeError("이미 렌더된 후보가 있습니다. 새로 만들지 말고 그것을 검토·복구하세요.")
    root.mkdir(parents=True, exist_ok=True)

    # A one-minute source retold as a one-minute Short is a re-upload, and its
    # fixed CTA reads "1분 짜리 영상 내용을 모두 정리했습니다".
    from cafe_manifest_publisher import measure_source_video
    from content_production_policy import validate_longform_source

    validate_longform_source(measure_source_video(source_key))
    # 사전점검은 자주 막히는 관문인데, 출력을 버리면 무엇이 막았는지 로그에 안 남는다.
    # 실패한 항목 이름을 예외 메시지에 실어서 다음 실패를 바로 읽을 수 있게 한다.
    preflight = subprocess.run(
        [sys.executable, str(PROJECT / "content_workflow_preflight.py"), "--runtime", "--json"],
        capture_output=True, text=True,
    )
    if preflight.returncode != 0:
        try:
            report = json.loads(preflight.stdout or "{}")
        except ValueError:
            report = {}
        failed = report.get("failures") or [
            name for name, passed in (report.get("checks") or {}).items() if not passed
        ]
        detail = ", ".join(str(name) for name in failed) or (
            (preflight.stderr or preflight.stdout or "").strip()[-200:])
        raise RuntimeError(f"제작 사전점검 실패: {detail}")

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

    # The comment CTA keyword is decided once, by notebooklm_shorts, and recorded
    # in cta-transform.json; the builder and the lineage gate both read it from
    # that bound file rather than re-deriving it.
    transform_path = root / "notebooklm/cta-transform.json"
    if not transform_path.is_file():
        raise RuntimeError("댓글 CTA 변환 증거(cta-transform.json)가 없습니다.")
    transform = json.loads(transform_path.read_text(encoding="utf-8"))
    if transform.get("status") != "cta_only" or transform.get("cta_style") != "comment_keyword":
        raise RuntimeError("댓글 CTA 변환 증거가 comment_keyword 계약과 다릅니다.")
    builder.SOURCE_MINUTES = int(match[1])
    builder.COMMENT_KEYWORD = scripts.validate_comment_keyword(
        str(transform.get("comment_keyword") or "")
    )
    sections = builder.split_seven_sections(script)

    answer_path = root / "notebooklm/notebooklm-answer-recovered.md"
    recovery_path = root / "notebooklm/notebooklm-answer-recovery-evidence.json"
    if answer_path.exists() != recovery_path.exists():
        raise RuntimeError("복구한 원응답과 그 증거는 함께 있어야 합니다.")
    if not answer_path.exists():
        answer_path = root / "notebooklm/notebooklm-answer.md"
    fact_path = root / "notebooklm" / scripts.FACT_VERIFICATION_FILENAME

    heads = scripts.extract_head_copy_candidates(answer_path.read_text(encoding="utf-8"))
    heads = _headcopy_order(heads, script)
    title = " ".join(scripts.head_copy_lines(heads[0]))
    presenter_binding = json.loads(PRESENTER_SOURCE.read_text(encoding="utf-8"))["render_inputs"]["presenter"]
    # 승인본 보관 위치는 정본이 정한다. 옛 매니페스트에 박힌 Downloads 경로가 비어도
    # 같은 해시의 승인본을 찾아 쓴다(해시는 아래 검증에서 다시 확인된다).
    presenter_found = policy.presenter_asset_path(Path(presenter_binding["path"]).name)
    if presenter_found is not None:
        presenter_binding = {**presenter_binding, "path": str(presenter_found)}
    policy.validate_presenter_asset(presenter_binding["path"])
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
            "cta_transform": binding(transform_path),
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
