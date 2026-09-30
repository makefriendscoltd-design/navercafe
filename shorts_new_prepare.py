"""Prepare a Shorts candidate for a source that has no scheduled predecessor.

The repair path binds every candidate to the exact video it replaces. A link the
owner submits for the first time has nothing to replace, so this builds the same
production manifest through the same gates, minus the predecessor binding, and
leaves scheduling to the publisher.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import content_production_policy as policy
from headcopy_digits import to_digits
import notebooklm_shorts as scripts
from shorts_repair_prepare import PROJECT, binding

# 카페 발행 큐에만 해당하는 검사. 쇼츠 제작은 이 큐를 쓰지 않는다.
CAFE_ONLY_CHECKS = {"runtime:cafe_queue_policy"}
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

    def duplicates(candidate: str) -> bool:
        first_line = scripts.head_copy_lines(candidate)[0]
        flat = re.sub(r"\s+", "", first_line)
        # 도입 문장을 그대로 옮긴 후보, 그리고 음성이 말하는 공식 문구를 화면에 또
        # 띄우는 후보를 같이 뒤로 보낸다. 화면은 음성이 하지 않는 말을 해야 한다.
        if any(word in flat for word in ("미쳤습니다", "대박입니다", "천재입니다")):
            return True
        return bool(opening) and flat.rstrip("!?.") == opening.rstrip("!?.")

    recent = _recent_headcopy_openers()

    def spells_out_numbers(candidate: str) -> bool:
        """화면 글자에 한글로 풀어 쓴 숫자가 남았는지 본다.

        음성은 TTS가 읽어야 해서 `세 시간`으로 쓰지만, 화면은 눈으로 읽으므로
        `3시간`이어야 한 눈에 들어온다. 지침(v23)이 그렇게 요구한다.
        """
        text = " ".join(scripts.head_copy_lines(candidate))
        return bool(SPELLED_NUMBER_RE.search(text)) and not re.search(r"\d", text)

    def repeats_recent(candidate: str) -> bool:
        """최근에 쓴 첫 줄과 같은 틀이면 목록에서 또 똑같아 보인다.

        `이 남자 미쳤습니다` 반복을 막았더니 `아직도 ~하나요?`가 그 자리를 채웠다.
        하루치 열 편 중 여섯 편이 같은 틀로 나온 날이 있었다(2026-09-29).
        """
        return _opener(scripts.head_copy_lines(candidate)[0]) in recent

    def rendersafe(candidate: str) -> bool:
        # 후보 추출 단계는 첫 후보만 90px 폭을 실측한다. 순서를 바꾸면 실측을 안 거친
        # 후보가 화면에 올라가 3줄로 접힐 수 있으므로, 바꿔 넣을 후보를 여기서 실측한다.
        try:
            scripts.validate_head_copy(candidate, measure_pixels=True)
        except RuntimeError:
            return False
        return True

    usable = [h for h in heads if not duplicates(h) and rendersafe(h)]
    numeric = [h for h in usable if not spells_out_numbers(h)] or usable
    varied = [h for h in numeric if not repeats_recent(h)]
    chosen = varied or usable
    return chosen + [h for h in heads if h not in chosen] if chosen else heads


# 화면에서 숫자로 보여야 하는 것들. 단위가 붙은 한글 수사만 잡는다.
SPELLED_NUMBER_RE = re.compile(
    r"(한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|백|천|만|억|일|이|삼|사|오|육|칠|팔|구|십)\s*"
    r"(시간|분|초|명|개|번|일|주|달|개월|년|원|배|퍼센트|프로|가지|단계|시|천|만|억)")


def _written_headcopy(script: str) -> list[str]:
    """에이전트가 쓴 헤드카피. 실패하면 빈 목록이라 기존 후보를 쓴다."""
    import shorts_headcopy

    try:
        written = shorts_headcopy.write_headcopy(
            script, recent=shorts_headcopy.recent_headcopy())
    except Exception:  # noqa: BLE001 - 헤드카피 때문에 제작을 멈추지 않는다
        return []
    return written


def _write_headcopy_file(path: Path, heads: list[str]) -> None:
    """고른 순서와 숫자 표기를 후보 파일에 반영한다."""
    lines = []
    for index, head in enumerate(heads, 1):
        first, second = scripts.head_copy_lines(head)
        lines.append(f"{index}. {first} / {second}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _opener(line: str) -> str:
    """첫 줄의 틀. 어미와 대상만 바꾼 같은 문형을 한 덩어리로 본다."""
    flat = re.sub(r"\s+", "", line)
    for pattern in ("아직도", "혼자", "그냥", "직접"):
        if flat.startswith(pattern):
            return pattern
    if flat.endswith("손해!") or flat.endswith("손해죠?"):
        return "손해"
    return flat[:6]


def _recent_headcopy_openers(limit: int = 6, project: Path = PROJECT) -> set[str]:
    """최근 만든 후보들이 화면 첫 줄에 쓴 틀."""
    picked = []
    for path in project.glob("outputs/*/shorts/production_manifest.json"):
        try:
            title = json.loads(path.read_text(encoding="utf-8"))["render_inputs"]["upload_title"]
        except (OSError, ValueError, KeyError):
            continue
        picked.append((path.stat().st_mtime, str(title).split(" ")[0:]))
    picked.sort(reverse=True)
    return {_opener(" ".join(words)) for _, words in picked[:limit]}


def _write_from_captions(source_key: str, root: Path, media: Path | None = None) -> dict | None:
    """자막을 받아 원고를 쓴다. 막히면 None을 돌려주고 호출부가 이 원본을 실패로 둔다.

    NotebookLM은 화면이 개편되면 멈추고, 후보당 기회가 한 번뿐이라 실패하면 그 영상을
    영영 못 쓰고, 브라우저 하나를 잡고 있어서 병렬 제작을 막는다. 자막은 그 셋이 모두
    없다. 지침과 뒤따르는 검사는 그대로 쓴다.
    """
    if policy.SHORTS_SCRIPT_SOURCE != "captions":
        return None
    import shorts_caption_source
    import shorts_script_writer

    try:
        captions = shorts_caption_source.fetch(source_key, root / "captions", media=media)
        return shorts_script_writer.write_to(root / "writer", captions)
    except Exception as exc:  # noqa: BLE001 - 자막이 없거나 구독이 막히면 이유를 남기고 실패로 둔다
        (root / "captions_fallback.txt").write_text(
            f"{datetime.now().astimezone().isoformat(timespec='seconds')} {exc}\n",
            encoding="utf-8")
        return None


def prepare(source_key: str, *, candidate_name: str = "shorts") -> dict:
    if not re.fullmatch(r"shorts(?:-[a-zA-Z0-9_-]+)?", candidate_name):
        raise ValueError("후보 이름은 shorts 또는 shorts-로 시작하는 단일 폴더명이어야 합니다.")
    source_root = _source_root(source_key)
    root = source_root / candidate_name
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
        # 카페 쪽 설정이 바뀌는 중이라고 쇼츠 제작이 멈추면 안 된다(2026-09-29에 실제로
        # 열 편이 그렇게 막혔다). 쇼츠가 쓰지 않는 검사는 실패로 세지 않는다.
        try:
            report = json.loads(preflight.stdout or "{}")
        except ValueError:
            report = {}
        failed = [name for name in (report.get("failures") or [
            name for name, passed in (report.get("checks") or {}).items() if not passed
        ]) if name not in CAFE_ONLY_CHECKS]
        blocking = bool(failed) or not report
        detail = ", ".join(str(name) for name in failed) or (
            (preflight.stderr or preflight.stdout or "").strip()[-200:])
        if not blocking:
            # 카페 큐 설정만 어긋난 경우다. 쇼츠는 그 큐를 쓰지 않으므로 계속 간다.
            print(f"사전점검 경고(쇼츠와 무관): {detail[:120]}", flush=True)
        if blocking:
            raise RuntimeError(f"제작 사전점검 실패: {detail}")

    source_video, credit = _download_source(source_key, root)
    if not credit.startswith("출처: ") or len(credit) <= 4:
        raise RuntimeError("원본 출처 표기가 없습니다.")

    script_path = root / "07_script_final.txt"
    if not script_path.exists():
        command = [
            sys.executable, str(PROJECT / "notebooklm_shorts.py"),
            "--url", f"https://youtu.be/{source_key}", "--out", str(script_path),
            "--headline-out", str(root / "06_headcopy_candidates.txt"),
            "--evidence-dir", str(root / "notebooklm"),
            "--preserve-authorized-wording",
        ]
        written = _write_from_captions(source_key, root, media=source_video)
        if not written:
            # 2026-09-30 사용자 결정: NotebookLM 예비 경로를 쓰지 않는다. 자막 원고가
            # 안 되면 이 원본은 실패로 두고 그날 자리는 다른 레퍼런스가 채운다.
            reason = (root / "captions_fallback.txt")
            detail = reason.read_text(encoding="utf-8").strip()[-200:] if reason.is_file() else ""
            raise RuntimeError(f"자막 원고 작성 실패(NotebookLM 예비 경로 없음): {detail}")
        command += ["--answer-file", written["answer"]]
        subprocess.run(command, check=True)

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
    # 자막 경로로 쓴 원고가 있으면 그것이 원고의 원본이다.
    written_answer = root / "writer/answer.md"
    from_captions = written_answer.is_file()
    if from_captions:
        answer_path = written_answer
    elif not answer_path.exists():
        answer_path = root / "notebooklm/notebooklm-answer.md"
    fact_path = root / "notebooklm" / scripts.FACT_VERIFICATION_FILENAME

    heads = scripts.extract_head_copy_candidates(answer_path.read_text(encoding="utf-8"))
    heads = [to_digits(head) for head in _headcopy_order(heads, script)]
    # 노트북 후보 셋은 틀에 박혀 나온다. 대본을 근거로 직접 쓴 것이 있으면 그것을 쓰고,
    # 에이전트가 막히거나 검사를 못 넘기면 노트북 후보로 돌아간다.
    # Caption writer already generated and validated these three candidates.
    # Rewriting them here wastes another model call and invalidates an editorial
    # review of the answer without changing the reviewed source script.
    written = [] if from_captions else _written_headcopy(script)
    if written:
        # 화면에 그려지는 것은 1안뿐이다. 직접 쓴 것을 앞에 두고 나머지 자리는 기존
        # 후보로 채운다. 렌더는 후보 셋을 요구하고 셋 다 형식 검사를 받는다.
        heads = (written + [h for h in heads if h not in written])[:3]
    # 렌더는 이 파일의 첫 후보를 화면에 그린다. 고른 순서를 파일에도 적어야 화면이 바뀐다.
    _write_headcopy_file(root / "06_headcopy_candidates.txt", heads)
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
            **({"mode": "caption_written", "answer": answer,
                "transcript": binding(root / "captions/transcript.txt"),
                "caption_evidence": binding(root / "captions/evidence.json"),
                "writer_evidence": binding(root / "writer/evidence.json")}
               if from_captions else {
                   "mode": "notebooklm_verbatim", "answer": answer,
                   "provider_evidence": binding(
                       root / "notebooklm/notebooklm-provider-evidence.json"),
                   **({"answer_recovery": binding(recovery_path),
                       "stored_answer": binding(root / "notebooklm/notebooklm-answer.md")}
                      if recovery_path.exists() else {})}),
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
    parser.add_argument("--candidate-name", default="shorts",
                        help="검토용 별도 후보 폴더. 기존 원고·영상·발행 증거를 보존한다")
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.source_key, candidate_name=args.candidate_name), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
