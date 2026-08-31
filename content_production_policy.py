"""Fail-closed production policy for Minsoo's three-channel content workflow.

The values in this module are the machine-readable counterpart of
``SHORTS_SPEC.md``.  Provider mutations must never proceed from a bundle that
does not satisfy these gates.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ASIDE_ACCOUNT = "u0"
CAFE_NOTEBOOK = {
    "title": "민수대표님_카페글",
    "id": "c09a56d4-b87c-4f54-bfdb-93219326fbae",
}
SHORTS_NOTEBOOK = {
    "title": "민수대표님_숏폼",
    "id": "ed70fc3b-474b-423a-9ca8-d19934703f27",
}
FORBIDDEN_NOTEBOOK_PREFIXES = ("그지마케팅_",)

MINSOO_VOICE_ID = "34bevfaPHev7LXnjGAlA"
MINSOO_MODEL_ID = "eleven_multilingual_v2"
MINSOO_VOICE_SETTINGS = {
    "stability": 0.65,
    "similarity_boost": 0.9,
    "style": 0.0,
    "use_speaker_boost": True,
}

TAILBITE = {"threshold_db": -35.0, "minimum": 0.08, "retained_gap": 0.06}
NARRATION = {
    "generation_mode": "seven_sections_same_voice_settings_for_speed_uniformity",
    "section_count": 7,
    "section_gap_seconds": 0.24,
    "last_to_first_pace_ratio_max": 1.10,
}
VIDEO = {"width": 1080, "height": 1920, "fps": 30}
HEADLINE = {"font_size": 90, "x": 540, "y": 440, "line_spacing": 20}
SUBTITLE = {
    "font_size": 59,
    "x": 540,
    "y": 940,
    "max_lines": 1,
    "merge_enabled": False,
    "rule": "one full token; no Latin/mixed-token slicing",
}
SUBTITLE_EDGE_PUNCTUATION = r".,，、:;!?！？。…~ㆍ·•\"'“”‘’()[]{}"
PRESENTER = {"x": 325, "y": 1298, "width": 430, "height": 430, "shape": "circle"}
SOURCE_SCREEN = {"x": 0, "y": 664, "width": 1080, "height": 608, "speed": 2.0}
WATERMARK = {"text": "@aimax", "x": 540, "y": 1768, "font_size": 44}

MINSOO_PRESENTER_ASSETS = {
    "2026-07-02 15-39-18.mp4": "a84c1f78ca5ac4fc2f45d67d7cb0d2e02f7a8dc25da5c71adae911a47f7b5056",
    "2026-06-29 16-32-49.mp4": "6f1382d51b0f9fc1c58634a3be3116d96dd6fa799da2958b7d05f87a6954b5c4",
    "2026-07-16 18-23-45.mp4": "ecb4d3638f9af4e63532943ecb777b8f8d144c30b44785effb032e6ccda90e08",
    "2026-07-16 18-21-06.mp4": "0cc94fb9cda999603980a0e817b0b6b95553a9c44d8f0e467308b1029b7de73f",
}

AUDIO_GATES = {
    "voice_integrated_lufs": -16.0,
    "voice_integrated_tolerance": 0.5,
    "voice_true_peak_max_dbtp": -2.0,
    "voice_minus_bgm_min_lu": 14.0,
    "voice_peak_minus_sfx_peak_min_db": 8.0,
    "final_integrated_lufs": -14.0,
    "final_integrated_tolerance": 0.5,
    "final_true_peak_max_dbtp": -1.8,
}

CARDNEWS = {"width": 1080, "height": 1080, "aspect_ratio": "1:1", "count": 10}
SCHEDULE = {
    "max_per_day": 2,
    "minimum_gap_hours": 5,
    "preferred_hours": (11, 20),
    "include_weekends": True,
}


class ProductionPolicyError(RuntimeError):
    """Raised before a nonconforming artifact can reach a provider."""


def strip_subtitle_edge_punctuation(token: str) -> str:
    """Remove sentence punctuation at token edges, preserving internal product marks."""
    return token.strip().strip(SUBTITLE_EDGE_PUNCTUATION).strip()


def validate_subtitle_file(path: str | Path) -> dict[str, int]:
    """Fail closed when an SRT caption retains sentence punctuation at either edge."""
    subtitle = Path(path)
    if not subtitle.is_file():
        raise ProductionPolicyError(f"쇼츠 자막 파일이 없습니다: {subtitle}")
    texts: list[str] = []
    for line in subtitle.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if not value or value.isdigit() or "-->" in value:
            continue
        texts.append(value)
    if not texts:
        raise ProductionPolicyError("쇼츠 자막 이벤트가 비어 있습니다.")
    violations = [text for text in texts if text != strip_subtitle_edge_punctuation(text)]
    if violations:
        raise ProductionPolicyError(
            f"쇼츠 자막 양끝 문장부호가 남아 있습니다: {len(violations)}개"
        )
    return {"caption_count": len(texts), "edge_punctuation_violations": 0}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductionPolicyError(f"검증 증거 JSON을 읽을 수 없습니다: {path}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_notebook_binding(kind: str, *, account: str, title: str, notebook_id: str) -> dict[str, str]:
    expected = CAFE_NOTEBOOK if kind == "cafe" else SHORTS_NOTEBOOK if kind == "shorts" else None
    if expected is None:
        raise ProductionPolicyError(f"알 수 없는 NotebookLM 용도입니다: {kind}")
    if account != ASIDE_ACCOUNT:
        raise ProductionPolicyError("NotebookLM은 Aside u0 로그인 세션만 사용합니다.")
    if title.startswith(FORBIDDEN_NOTEBOOK_PREFIXES):
        raise ProductionPolicyError("그지마케팅 NotebookLM 노트북은 이 제작 흐름에서 금지됩니다.")
    if title != expected["title"] or notebook_id != expected["id"]:
        raise ProductionPolicyError(f"{kind} NotebookLM 정본 제목/ID가 일치하지 않습니다.")
    return {"account": account, "title": title, "id": notebook_id}


def validate_presenter_asset(path: str | Path) -> dict[str, str]:
    asset = Path(path).expanduser().resolve()
    expected = MINSOO_PRESENTER_ASSETS.get(asset.name)
    if not asset.is_file() or not expected:
        raise ProductionPolicyError("승인된 민수 촬영본 4개 중 하나가 아닙니다.")
    actual = _sha256(asset)
    if actual != expected:
        raise ProductionPolicyError("민수 촬영본 파일 해시가 승인값과 다릅니다.")
    return {"path": str(asset), "sha256": actual}


def validate_voice_evidence(payload: dict[str, Any]) -> None:
    if payload.get("voice_id") != MINSOO_VOICE_ID or payload.get("model_id") != MINSOO_MODEL_ID:
        raise ProductionPolicyError("민수 Voice ID/모델이 정본과 다릅니다.")
    settings = payload.get("settings") or payload.get("voice_settings")
    if settings != MINSOO_VOICE_SETTINGS:
        raise ProductionPolicyError("민수 음성 설정값이 정본과 다릅니다.")
    alignment = payload.get("alignment") or {}
    if not alignment.get("characters") or not alignment.get("character_start_times_seconds"):
        raise ProductionPolicyError("민수 발음 정렬값이 비어 있습니다.")
    if payload.get("generation_mode") != NARRATION["generation_mode"]:
        raise ProductionPolicyError("민수 음성은 도입·첫째~다섯째·CTA 7구간 합성본이어야 합니다.")
    if payload.get("section_count") != NARRATION["section_count"]:
        raise ProductionPolicyError("민수 음성 구간 수가 정본 7개와 다릅니다.")


def validate_exact_runtime_evidence(payload: dict[str, Any]) -> None:
    if payload.get("status") != "pass":
        raise ProductionPolicyError("정확 대본 런타임 게이트 상태가 pass가 아닙니다.")
    for key in ("script_alignment_hash_match", "caption_count_matches_script_tokens", "caption_tokens_are_whole"):
        if payload.get(key) is not True:
            raise ProductionPolicyError(f"정확 대본 런타임 게이트 실패: {key}")
    pace = payload.get("pace_uniformity") or {}
    ratio = pace.get("last_to_first_ratio")
    if pace.get("status") != "pass" or not isinstance(ratio, (int, float)):
        raise ProductionPolicyError("초반·후반 말하기 속도 균일성 증거가 없습니다.")
    if ratio > NARRATION["last_to_first_pace_ratio_max"]:
        raise ProductionPolicyError("후반 대사 속도가 초반보다 10% 넘게 빨라졌습니다.")


def validate_render_evidence(payload: dict[str, Any]) -> None:
    required_scalars = {
        "output_width": VIDEO["width"],
        "output_height": VIDEO["height"],
        "fps": VIDEO["fps"],
    }
    for key, expected in required_scalars.items():
        if payload.get(key) != expected:
            raise ProductionPolicyError(f"렌더 설정 {key}가 정본과 다릅니다.")
    for section, expected in (
        ("title", HEADLINE),
        ("subtitle", SUBTITLE),
        ("presenter", PRESENTER),
        ("screen", SOURCE_SCREEN),
        ("watermark", WATERMARK),
    ):
        actual = payload.get(section) or {}
        for key, value in expected.items():
            if key == "rule":
                continue
            if actual.get(key) != value:
                raise ProductionPolicyError(f"렌더 설정 {section}.{key}가 정본과 다릅니다.")
    provenance = payload.get("render_provenance") or {}
    if provenance.get("source_and_presenter_audio_mapped") is not False:
        raise ProductionPolicyError("원본/민수 촬영본 오디오는 최종 믹스에 매핑하면 안 됩니다.")
    if provenance.get("source_audio_mapped") is not False or provenance.get("presenter_audio_mapped") is not False:
        raise ProductionPolicyError("원본 영상 또는 민수 촬영본 오디오가 매핑됐습니다.")
    presenter_name = Path(str(provenance.get("presenter_input") or "")).name
    presenter_hash = str(provenance.get("presenter_sha256") or "")
    if MINSOO_PRESENTER_ASSETS.get(presenter_name) != presenter_hash:
        raise ProductionPolicyError("민수 원형 PIP 출처가 승인된 촬영본이 아닙니다.")
    if not provenance.get("source_footage") or not provenance.get("source_footage_sha256"):
        raise ProductionPolicyError("원본 YouTube 영상 출처/해시 증거가 없습니다.")
    restored = provenance.get("v7_reference_restoration") or {}
    if restored.get("voice_settings") != MINSOO_VOICE_SETTINGS:
        raise ProductionPolicyError("V7 음성 정본 복원 증거가 다릅니다.")
    if restored.get("headline_font_size_1080") != HEADLINE["font_size"]:
        raise ProductionPolicyError("V7 헤드카피 90px 증거가 없습니다.")
    if restored.get("subtitle_rule") != SUBTITLE["rule"]:
        raise ProductionPolicyError("V7 자막 전체 토큰 규칙 증거가 없습니다.")


REQUIRED_MACHINE_GATES = (
    "voice_integrated_minus16_tolerance_0_5",
    "voice_true_peak_at_most_minus2",
    "voice_minus_bgm_at_least14_lu",
    "voice_peak_minus_sfx_peak_at_least8_db",
    "final_integrated_minus14_tolerance_0_5",
    "final_true_peak_at_most_minus1_8",
    "full_decode",
    "exact_script_narration_caption_and_config_hashes",
)


def validate_machine_evidence(payload: dict[str, Any]) -> None:
    if payload.get("status") != "pass" or payload.get("failures"):
        raise ProductionPolicyError("쇼츠 머신 검증 상태가 pass가 아닙니다.")
    gates = payload.get("gates") or {}
    missing = [name for name in REQUIRED_MACHINE_GATES if gates.get(name) is not True]
    if missing:
        raise ProductionPolicyError("필수 쇼츠 머신 게이트 실패: " + ", ".join(missing))
    if gates.get("source_audio_mapped") is not False or gates.get("presenter_audio_mapped") is not False:
        raise ProductionPolicyError("음소거되어야 할 원본/PIP 오디오가 매핑됐습니다.")


def validate_shorts_bundle_for_upload(video_path: str | Path) -> dict[str, str]:
    """Require V7 render, voice, and machine evidence before any Studio upload."""
    video = Path(video_path).expanduser().resolve()
    if not video.is_file() or video.stat().st_size <= 0:
        raise ProductionPolicyError(f"쇼츠 MP4가 없거나 비어 있습니다: {video}")
    root = video.parent
    render_path = root / "render_config.json"
    machine_path = root / "machine_validation.json"
    runtime_path = root / "02_exact_runtime_gate.json"
    subtitle_path = root / "captions.srt"
    alignment_candidates = (
        root / "narration_alignment.json",
        root / "09_shorts_minsoo_alignment.json",
        root / "05_minsoo_alignment.json",
    )
    alignment_path = next((path for path in alignment_candidates if path.is_file()), None)
    missing = [str(path.name) for path in (render_path, machine_path, runtime_path, subtitle_path) if not path.is_file()]
    if alignment_path is None:
        missing.append("narration_alignment.json")
    if missing:
        raise ProductionPolicyError("쇼츠 업로드 필수 검증 증거가 없습니다: " + ", ".join(missing))
    validate_render_evidence(_load_json(render_path))
    validate_machine_evidence(_load_json(machine_path))
    validate_voice_evidence(_load_json(alignment_path))
    validate_exact_runtime_evidence(_load_json(runtime_path))
    validate_subtitle_file(subtitle_path)
    return {
        "video": str(video),
        "video_sha256": _sha256(video),
        "render_config": str(render_path),
        "machine_validation": str(machine_path),
        "exact_runtime_gate": str(runtime_path),
        "voice_alignment": str(alignment_path),
        "subtitles": str(subtitle_path),
    }


def validate_schedule(slots: Iterable[datetime]) -> list[datetime]:
    ordered = sorted(slots)
    by_date: dict[Any, list[datetime]] = {}
    for slot in ordered:
        if slot.tzinfo is None:
            raise ProductionPolicyError("예약 시각에는 Asia/Seoul 오프셋이 필요합니다.")
        by_date.setdefault(slot.date(), []).append(slot)
    if any(len(items) > SCHEDULE["max_per_day"] for items in by_date.values()):
        raise ProductionPolicyError("쇼츠는 하루 최대 2개만 예약합니다.")
    for previous, current in zip(ordered, ordered[1:]):
        if (current - previous).total_seconds() < SCHEDULE["minimum_gap_hours"] * 3600:
            raise ProductionPolicyError("쇼츠 예약 간격은 최소 5시간이어야 합니다.")
    return ordered


def validate_replacement_sequence(events: Iterable[str]) -> None:
    """Protect provider replacements: verify new first, delete old, then CRM."""
    values = list(events)
    required = ["new_provider_verified", "old_cancelled_or_hidden", "old_deleted", "crm_sent"]
    positions = []
    for event in required:
        if event not in values:
            raise ProductionPolicyError(f"교체 절차 증거 누락: {event}")
        positions.append(values.index(event))
    if positions != sorted(positions):
        raise ProductionPolicyError("교체 순서는 새 게시 확인 → 기존 취소/숨김 → 삭제 → CRM입니다.")
