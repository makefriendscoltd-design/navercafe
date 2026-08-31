"""Render and validate a vertical Shorts video from the approved script/assets."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from build_tailbite_audio import (
    build_captions,
    build_segments,
    detect_silences,
    render_audio,
    write_srt,
)

from notebooklm_shorts import (
    head_copy_lines,
    require_strong_hook,
    validate_head_copy,
    validate_head_copy_connection,
)
from content_production_policy import (
    HEADLINE,
    MINSOO_MODEL_ID,
    MINSOO_VOICE_ID,
    MINSOO_VOICE_SETTINGS,
    TAILBITE,
    validate_shorts_bundle_for_upload,
    validate_voice_evidence,
)


MAX_SHORT_SECONDS = 180.0
HEADLINE_FONT = Path(
    "/Users/apple/orca/projects/ccidainsta/fonts/BMHANNA11yrs/BMHANNA_11yrs_ttf.ttf"
)


def normalize_shorts_title(title: str) -> str:
    """Remove trailing upload hashtags; the Shorts title is copy, not a tag list."""
    text = (title or "").strip()
    text = re.sub(r"(?:\s+#[^\s#]+)+\s*$", "", text).strip()
    if not text:
        raise RuntimeError("쇼츠 업로드 제목이 비어 있습니다.")
    return text[:100].rstrip()


def _run(command: list[str], *, timeout: int = 900) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "명령 실행 실패").strip()
        raise RuntimeError(detail[-4000:])
    return result


def _probe(path: Path) -> dict:
    result = _run(
        [
            "ffprobe", "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        ],
        timeout=60,
    )
    return json.loads(result.stdout)


def _duration(path: Path) -> float:
    return float(_probe(path).get("format", {}).get("duration") or 0)


def _yaml_scalar(path: Path, section: str, key: str) -> str:
    """Read one simple nested YAML scalar without exposing the config."""
    if not path.is_file():
        return ""
    section_indent: int | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if section_indent is None:
            if stripped == f"{section}:":
                section_indent = indent
            continue
        if indent <= section_indent:
            break
        match = re.match(rf"{re.escape(key)}\s*:\s*(.+?)\s*$", stripped)
        if match:
            return match.group(1).strip().strip("'\"")
    return ""


def _env_scalar(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(rf"\s*(?:export\s+)?{re.escape(key)}\s*=\s*(.+?)\s*$", raw)
        if match:
            return match.group(1).strip().strip("'\"")
    return ""


def _elevenlabs_api_keys() -> list[str]:
    candidates = [os.environ.get("ELEVENLABS_API_KEY", "").strip()]
    for path in (
        Path("/Users/apple/orca/projects/ccidainsta/config.yaml"),
        Path("/Users/apple/orca/projects/ccidainsta_family/config.yaml"),
        Path("/Users/apple/orca/projects/momcuinsta/config.yaml"),
    ):
        candidates.append(_yaml_scalar(path, "api_keys", "elevenlabs"))
    for path in (
        Path("/Users/apple/orca/projects/coupang/server/.env"),
        Path("/Users/apple/orca/projects/ccidainsta_family/.env"),
    ):
        candidates.append(_env_scalar(path, "ELEVENLABS_API_KEY"))
    keys: list[str] = []
    for candidate in candidates:
        if candidate and "여기에" not in candidate and candidate not in keys:
            keys.append(candidate)
    return keys


def _generate_minsoo_take(script: str, audio_path: Path, alignment_path: Path) -> None:
    """Generate the approved voice plus timestamps; never substitute a voice."""
    payload = json.dumps(
        {
            "text": script,
            "model_id": MINSOO_MODEL_ID,
            "voice_settings": MINSOO_VOICE_SETTINGS,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    keys = _elevenlabs_api_keys()
    if not keys:
        raise RuntimeError(
            "민수 음성용 ELEVENLABS_API_KEY를 찾지 못했습니다. "
            "다른 음성으로 대체하지 않고 쇼츠 생성을 중단합니다."
        )
    result = None
    last_error: Exception | None = None
    for key in keys:
        request = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{MINSOO_VOICE_ID}/with-timestamps"
            "?output_format=mp3_44100_192",
            data=payload,
            headers={"Content-Type": "application/json", "xi-api-key": key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                result = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 401:
                continue
            raise RuntimeError(
                "민수 ElevenLabs 음성 생성에 실패했습니다. 다른 음성으로 대체하지 않습니다."
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "민수 ElevenLabs 음성 생성에 실패했습니다. 다른 음성으로 대체하지 않습니다."
            ) from exc
    if result is None:
        raise RuntimeError(
            "저장된 ElevenLabs 키가 모두 인증되지 않았습니다. "
            "다른 음성으로 대체하지 않고 쇼츠 생성을 중단합니다."
        ) from last_error
    audio_base64 = result.get("audio_base64")
    alignment = result.get("normalized_alignment") or result.get("alignment")
    if not audio_base64 or not alignment:
        raise RuntimeError("민수 음성 또는 발음 정렬값이 없어 쇼츠 생성을 중단합니다.")
    audio_path.write_bytes(base64.b64decode(audio_base64))
    alignment_path.write_text(
        json.dumps(
            {
                "voice_id": MINSOO_VOICE_ID,
                "model_id": MINSOO_MODEL_ID,
                "settings": MINSOO_VOICE_SETTINGS,
                "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
                "alignment": alignment,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _build_minsoo_tailbite(script_path: Path, root: Path) -> tuple[Path, Path, Path]:
    source_audio = root / "09_shorts_minsoo_single_take.mp3"
    alignment_path = root / "09_shorts_minsoo_alignment.json"
    master_audio = root / "09_shorts_narration.mp3"
    srt_path = root / "09_shorts_final.srt"
    manifest_path = root / "09_shorts_narration.manifest.json"
    script = script_path.read_text(encoding="utf-8").strip()
    _generate_minsoo_take(script, source_audio, alignment_path)
    source_duration = _duration(source_audio)
    silences = detect_silences(
        source_audio,
        threshold_db=TAILBITE["threshold_db"],
        minimum=TAILBITE["minimum"],
    )
    segments = build_segments(source_duration, silences, retained_gap=TAILBITE["retained_gap"])
    render_audio(source_audio, master_audio, segments)
    captions = build_captions(script_path, alignment_path, segments, max_chars=6)
    write_srt(srt_path, captions)
    manifest_path.write_text(
        json.dumps(
            {
                "voice_id": MINSOO_VOICE_ID,
                "model_id": MINSOO_MODEL_ID,
                "source_duration": source_duration,
                "output_duration": _duration(master_audio),
                "threshold_db": TAILBITE["threshold_db"],
                "min_silence": TAILBITE["minimum"],
                "retained_gap": TAILBITE["retained_gap"],
                "caption_mode": "one_full_token_no_midword_split",
                "caption_count": len(captions),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return master_audio, srt_path, manifest_path


def validate_short_video(path: str | Path, *, max_seconds: float = MAX_SHORT_SECONDS) -> dict:
    video = Path(path).expanduser().resolve()
    if not video.is_file() or video.stat().st_size <= 0:
        raise RuntimeError(f"쇼츠 MP4가 없거나 비어 있습니다: {video}")
    probe = _probe(video)
    streams = probe.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(probe.get("format", {}).get("duration") or 0)
    errors = []
    if not video_stream:
        errors.append("영상 스트림 없음")
    elif int(video_stream.get("height") or 0) <= int(video_stream.get("width") or 0):
        errors.append("세로 영상이 아님")
    if video_stream and (
        int(video_stream.get("width") or 0) != 1080
        or int(video_stream.get("height") or 0) != 1920
    ):
        errors.append(
            f"해상도 {video_stream.get('width')}x{video_stream.get('height')} (1080x1920 필요)"
        )
    if not audio_stream:
        errors.append("오디오 스트림 없음")
    if not (0 < duration <= max_seconds):
        errors.append(f"길이 {duration:.2f}초 (0초 초과 {max_seconds:.0f}초 이하 필요)")
    if errors:
        raise RuntimeError("쇼츠 MP4 검증 실패: " + " | ".join(errors))
    return {
        "path": str(video),
        "width": int(video_stream["width"]),
        "height": int(video_stream["height"]),
        "duration": round(duration, 3),
        "video_codec": video_stream.get("codec_name", ""),
        "audio_codec": audio_stream.get("codec_name", ""),
        "size": video.stat().st_size,
    }


def validate_minsoo_voice_artifact(path: str | Path) -> dict:
    """Require Minsoo provider metadata beside a Shorts file before upload."""
    target = Path(path).expanduser().resolve()
    root = target if target.is_dir() else target.parent
    preferred = [
        root / "09_shorts_minsoo_alignment.json",
        root / "05_minsoo_alignment.json",
    ]
    alignment_path = next((candidate for candidate in preferred if candidate.is_file()), None)
    if alignment_path is None:
        matches = sorted(root.glob("*minsoo*alignment*.json"))
        alignment_path = matches[-1] if matches else None
    if alignment_path is None:
        raise RuntimeError("민수 Voice ID 증거 파일이 없어 쇼츠 업로드를 중단합니다.")
    try:
        payload = json.loads(alignment_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("민수 음성 증거 파일을 읽을 수 없어 업로드를 중단합니다.") from exc
    try:
        validate_voice_evidence(payload)
    except RuntimeError as exc:
        raise RuntimeError(f"승인된 민수 음성 증거와 일치하지 않아 쇼츠 업로드를 중단합니다: {exc}") from exc
    voice_id = str(payload.get("voice_id") or "")
    model_id = str(payload.get("model_id") or "")
    return {
        "voice_id": voice_id,
        "model_id": model_id,
        "alignment": str(alignment_path),
    }


def _escape_subtitles_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "\\\\").replace(":", "\\:")
    return value.replace("'", "\\'").replace(",", "\\,").replace("[", "\\[").replace("]", "\\]")


def _headline_drawtext_filter(headline: str, root: Path) -> tuple[str, Path]:
    """Build the persistent cyan two-line first-screen headline overlay."""
    normalized = validate_head_copy(headline)
    first, second = head_copy_lines(normalized)
    headline_path = root / "08_shorts_headline.txt"
    headline_path.write_text(f"{first}\n{second}\n", encoding="utf-8")
    first_path = root / ".08_shorts_headline_1.txt"
    second_path = root / ".08_shorts_headline_2.txt"
    first_path.write_text(first, encoding="utf-8")
    second_path.write_text(second, encoding="utf-8")
    font = HEADLINE_FONT if HEADLINE_FONT.is_file() else Path(
        "/System/Library/Fonts/AppleSDGothicNeo.ttc"
    )
    common = (
        f"fontfile='{_escape_subtitles_path(font)}':fontcolor=0x22D9F2:"
        f"fontsize={HEADLINE['font_size']}:borderw=3:bordercolor=0x000000:"
        "box=1:boxcolor=black@0.58:boxborderw=22:x=(w-text_w)/2"
    )
    filters = (
        f"drawtext={common}:textfile='{_escape_subtitles_path(first_path)}':y=118,"
        f"drawtext={common}:textfile='{_escape_subtitles_path(second_path)}':y=222"
    )
    return filters, headline_path


def render_short_video(
    script: str,
    image_paths: list[str | Path],
    out_dir: str | Path,
    *,
    title: str = "",
    description: str = "",
    headline: str = "",
    headline_candidates: list[str] | None = None,
) -> dict:
    """Block the retired frame-slideshow renderer.

    Production Shorts must use the approved V7 renderer with original source
    footage and one of the four verified Minsoo presenter clips.  Keeping this
    entry point fail-closed prevents a missing PIP or cardnews/frame collage
    from reaching YouTube.
    """
    raise RuntimeError(
        "기존 프레임 슬라이드 쇼츠 렌더러는 폐기됐습니다. "
        "V7 원본영상+민수 원형 PIP 렌더러와 검증 증거를 사용하세요."
    )


def validate_upload_ready(path: str | Path) -> dict:
    """Public pre-provider gate shared by all upload entry points."""
    validate_short_video(path)
    return validate_shorts_bundle_for_upload(path)


def _retired_render_short_video(
    script: str,
    image_paths: list[str | Path],
    out_dir: str | Path,
    *,
    title: str = "",
    description: str = "",
    headline: str = "",
    headline_candidates: list[str] | None = None,
) -> dict:
    """Retained temporarily for reference only; never called in production."""
    require_strong_hook(script)
    normalized_headline = validate_head_copy_connection(headline, script)
    for executable in ("ffmpeg", "ffprobe"):
        if not shutil.which(executable):
            raise RuntimeError(f"필수 실행 파일을 찾을 수 없습니다: {executable}")
    frames = [Path(p).expanduser().resolve() for p in image_paths]
    frames = [p for p in frames if p.is_file() and p.stat().st_size > 0]
    if not frames:
        raise RuntimeError("쇼츠에 사용할 원본 영상 프레임이 없습니다.")

    root = Path(out_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    headline_filter, headline_path = _headline_drawtext_filter(normalized_headline, root)
    script_path = root / "07_shorts_script_through_fifth.txt"
    if not script_path.exists() or script_path.read_text(encoding="utf-8").strip() != script.strip():
        script_path.write_text(script.strip() + "\n", encoding="utf-8")
    loop_path = root / ".09_shorts_visual_loop.mp4"
    output_path = root / "09_shorts_final.mp4"
    metadata_path = root / "10_shorts_upload.json"

    audio_path, srt_path, narration_manifest = _build_minsoo_tailbite(script_path, root)
    audio_duration = _duration(audio_path)
    if not (0 < audio_duration <= MAX_SHORT_SECONDS - 0.2):
        raise RuntimeError(f"쇼츠 내레이션 길이가 허용 범위를 벗어났습니다: {audio_duration:.2f}초")
    scene_seconds = 1.5
    inputs: list[str] = []
    filters: list[str] = []
    for index, frame in enumerate(frames):
        inputs.extend(["-loop", "1", "-t", str(scene_seconds), "-i", str(frame)])
        filters.append(
            f"[{index}:v]split=2[bg{index}][fg{index}];"
            f"[bg{index}]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,gblur=sigma=36[blur{index}];"
            f"[fg{index}]scale=1000:1700:force_original_aspect_ratio=decrease[front{index}];"
            f"[blur{index}][front{index}]overlay=(W-w)/2:(H-h)/2,"
            f"zoompan=z='min(zoom+0.0012,1.08)':d={math.ceil(scene_seconds*30)}:"
            f"s=1080x1920:fps=30,format=yuv420p,setpts=PTS-STARTPTS[v{index}]"
        )
    filters.append(
        "".join(f"[v{i}]" for i in range(len(frames)))
        + f"concat=n={len(frames)}:v=1:a=0[vout]"
    )
    _run(
        [
            "ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters),
            "-map", "[vout]", "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "22", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(loop_path),
        ]
    )

    subtitle_filter = (
        f"subtitles=filename='{_escape_subtitles_path(srt_path)}':"
        "force_style='FontName=Apple SD Gothic Neo,FontSize=19,Bold=1,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,BorderStyle=1,"
        "Outline=3,Shadow=1,Alignment=2,MarginV=210'"
    )
    _run(
        [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(loop_path),
            "-i", str(audio_path), "-t", f"{audio_duration:.3f}", "-vf",
            f"{subtitle_filter},{headline_filter}",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-shortest", "-movflags", "+faststart", str(output_path),
        ]
    )
    loop_path.unlink(missing_ok=True)
    (root / ".08_shorts_headline_1.txt").unlink(missing_ok=True)
    (root / ".08_shorts_headline_2.txt").unlink(missing_ok=True)
    validation = validate_short_video(output_path)
    metadata = {
        "title": normalize_shorts_title(
            title or "AI 티 나는 모션 그래픽을 바꾸는 5가지"
        ),
        "description": description or "클로드로 AI 티 나는 모션 그래픽을 바꾸는 5가지를 정리했습니다.\n\n#AI자동화 #Claude #Shorts",
        "headline": normalized_headline,
        "headline_candidates": headline_candidates or [normalized_headline],
        "video": str(output_path),
        "srt": str(srt_path),
        "voice_id": MINSOO_VOICE_ID,
        "voice_model": MINSOO_MODEL_ID,
        "narration_manifest": str(narration_manifest),
        "validation": validation,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        **validation,
        "voice_id": MINSOO_VOICE_ID,
        "voice_model": MINSOO_MODEL_ID,
        "headline": normalized_headline,
        "headline_path": str(headline_path),
        "srt": str(srt_path),
        "metadata": str(metadata_path),
    }
