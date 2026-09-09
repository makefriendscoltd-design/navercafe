"""Build the first Cafe manifest for a source that has none yet.

``content_workflow.py prepare-cafe`` rebuilds an existing entry from its own
manifest, so a link submitted for the first time has nothing to rebuild from.
This assembles that first manifest: the preserved NotebookLM answer becomes the
body through the same formatter, five frames come from the source video, and the
fixed tail is the one the board requires. It publishes nothing -- the queue
automation stays the only thing that posts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from content_lineage import cafe_body_lineage, clean_cafe_answer
from notebook_cafe_auto import build_body, count_sections

PROJECT = Path(__file__).resolve().parent
CATEGORY = "AI 자동화&수익화 정보"
CTA_TEXT = (
    "AI 자동화를 직접 배우는 오프라인 스터디를 진행하고 있습니다. "
    "관심 있으시면 아래 패밀리데이 모집 안내 글을 읽어보세요."
)
FAMILY_DAY_URL = "https://cafe.naver.com/westudyssat/4188"
IMAGE_COUNT = 5


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mark_headings(cleaned: str) -> tuple[str, list[str]]:
    """Mark the answer's own section titles so the formatter can see them.

    NotebookLM returns the Cafe answer as bare lines, so the section titles carry
    no marker of their own. Recover them exactly as the repair path does, keeping
    their words untouched -- a generated heading would no longer be the answer.
    """
    if count_sections(cleaned) == IMAGE_COUNT:
        return cleaned, []
    lines = cleaned.splitlines()
    headings = [
        line.strip() for line in lines
        if 4 <= len(line.strip()) <= 45 and not re.search(r"[.!?。]$|^\d+$", line.strip())
    ]
    if len(headings) != IMAGE_COUNT:
        raise RuntimeError(f"원문에서 소제목 {IMAGE_COUNT}개를 찾지 못했습니다 ({len(headings)}개).")
    marked = "\n\n".join(
        ("## " + line.strip()) if line.strip() in headings else line.strip()
        for line in lines if line.strip()
    )
    return marked, headings


def _extract_frames(video: Path, out_dir: Path, duration: float) -> list[Path]:
    """Take one frame per fifth of the video, skipping the intro and outro."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for index in range(IMAGE_COUNT):
        target = out_dir / f"youtube-frame-{index + 1:02d}.jpg"
        if not target.exists():
            at = duration * (index + 1) / (IMAGE_COUNT + 1)
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.3f}",
                 "-i", str(video), "-frames:v", "1", "-q:v", "2", "-y", str(target)],
                check=True,
            )
        if not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError(f"프레임 추출에 실패했습니다: {target}")
        frames.append(target)
    return frames


def prepare(source_key: str, *, title: str | None = None) -> dict:
    roots = [p for p in (PROJECT / "outputs").glob(source_key + "-*") if p.is_dir()]
    if len(roots) != 1:
        raise RuntimeError("원본의 공동 산출물 루트가 정확히 1개여야 합니다.")
    root = roots[0]
    cafe = root / "cafe"
    manifest_path = cafe / "06_cafe_manifest.json"
    if manifest_path.exists():
        raise RuntimeError("카페 매니페스트가 이미 있습니다. 새로 만들지 말고 그것을 쓰세요.")

    # Measure before building anything: a Short summarised into a column reads as
    # a column and only shows itself in the vertical frames it uses as images.
    from cafe_manifest_publisher import measure_source_video
    from content_production_policy import validate_longform_source

    source_shape = validate_longform_source(measure_source_video(source_key))

    answer_path = cafe / "notebooklm/notebooklm-answer.md"
    provider_path = cafe / "notebooklm/notebooklm-provider-evidence.json"
    for path in (answer_path, provider_path):
        if not path.is_file():
            raise RuntimeError(f"카페 NotebookLM 증거가 없습니다: {path}")

    cleaned = clean_cafe_answer(answer_path.read_text(encoding="utf-8"))
    marked, headings = _mark_headings(cleaned)
    body = build_body(marked, IMAGE_COUNT, {}, use_ai_keywords=False)
    if body.count("[BLOCKQUOTE]") != IMAGE_COUNT or body.count("[IMAGE_HERE]") != IMAGE_COUNT:
        raise RuntimeError("원문 소제목/이미지 구조가 다섯 구간을 만들지 못했습니다.")
    body_path = cafe / "03_cafe_body.txt"
    body_path.write_text(body, encoding="utf-8")
    lineage = cafe_body_lineage(answer_path, body_path)

    shorts_root = root / "shorts"
    video = shorts_root / "source_original.mp4"
    if not video.is_file():
        raise RuntimeError("원본 영상이 없습니다. 쇼츠 준비를 먼저 실행하세요.")
    download = json.loads((shorts_root / "source_download_evidence.json").read_text(encoding="utf-8"))
    frames = _extract_frames(video, cafe / "youtube_frames", float(download["duration_seconds"]))

    quotes = re.findall(r"\[BLOCKQUOTE\](.*?)\[/BLOCKQUOTE\]", body, re.S)
    manifest = {
        "schema_version": "1.0",
        "source_key": source_key,
        "title": title or headings[0] if headings else download["title"],
        "category": CATEGORY,
        "status": "candidate_requires_editor_and_approval_validation",
        "provider_mutation": False,
        "provider_editor_opened": False,
        "source_url": f"https://youtu.be/{source_key}",
        "source_long_url": f"https://www.youtube.com/watch?v={source_key}",
        "source_video": source_shape,
        "body_file": body_path.name,
        "notebooklm_answer": str(answer_path.resolve()),
        "notebooklm_provider_evidence": str(provider_path.resolve()),
        "source_dependencies": [
            {"path": "../shorts/source_download_evidence.json",
             "sha256": _sha256(shorts_root / "source_download_evidence.json")},
        ],
        "expected_quotes": IMAGE_COUNT,
        "expected_images": IMAGE_COUNT,
        "expected_quote_texts": quotes,
        "images": [str(frame.relative_to(cafe)) for frame in frames],
        "image_labels": headings or quotes,
        "content_lineage": lineage,
        "tail": {
            "cta_text": CTA_TEXT,
            "family_day_url": FAMILY_DAY_URL,
            "family_day_dom_kind": "og_card",
            "source_label": "▶ 원본 영상",
            "source_url": f"https://youtu.be/{source_key}",
            "source_long_url": f"https://www.youtube.com/watch?v={source_key}",
            "source_dom_kind": "youtube_preview",
            "expected_order": ["cta_text", "family_day_raw_url", "family_day_og_card",
                               "source_label", "source_raw_url", "youtube_preview"],
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (cafe / "11_local_validation.json").write_text(json.dumps({
        "status": "pass", "scope": "content_and_structure_only", "source_key": source_key,
        "checks": {"original_body_preserved": True, "five_quotes": True, "five_image_markers": True},
        "asset_status": "present",
        "evidence": lineage, "provider_mutation": False,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "cafe_candidate_prepared", "source_key": source_key,
            "manifest": str(manifest_path), "title": manifest["title"],
            "quotes": len(quotes), "images": len(frames)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_key")
    parser.add_argument("--title")
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.source_key, title=args.title), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
