"""Audit one produced source across all three channels before calling it done.

Production reported success when its commands exited zero. That is not the same
as the artefacts being right, and the difference is why the same job kept coming
out differently: the community body was never written at all, card decks carried
no source anchor, Shorts went up under their upload sentinel, and a Cafe column
was summarised from a 54-second vertical Short. Every one of those runs exited
zero.

This reads the finished artefacts and answers one question per channel: is this
publishable. It writes nothing; missing source metadata may be queried read-only.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parent

# The Shorts CTA names the source length, so it is also where a wrong source
# shows itself: a one-minute video cannot have "5가지" worth of items in it.
CTA_PATTERN = re.compile(r"(?m)^(\d+)분 짜리 영상 내용을 모두 정리했습니다\.")
SENTINEL_TITLE = re.compile(r"^shorts-[A-Za-z0-9_-]{11}-[0-9a-f]{12}$")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check_source(root: Path) -> list[str]:
    """The one talk all three channels retell has to be a longform talk."""
    from cafe_manifest_publisher import measure_source_video
    from content_production_policy import ProductionPolicyError, validate_longform_source

    source_key = root.name.rsplit("-", 1)[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", source_key):
        return ["공동 산출물 루트의 원본 ID를 확인할 수 없음"]
    manifest_path = root / "cafe/06_cafe_manifest.json"
    shape = None
    try:
        if manifest_path.is_file():
            # A failed Cafe draft must not block an independently valid Short.
            # Malformed Cafe JSON belongs to its channel gate; source identity
            # can still be measured from the common source key.
            try:
                manifest = _read(manifest_path)
            except (ValueError, OSError):
                manifest = {}
            if manifest.get("source_key") not in {None, source_key}:
                return ["카페 매니페스트와 공동 원본 ID 불일치"]
            shape = manifest.get("source_video")
        validate_longform_source(shape or measure_source_video(source_key))
    except ProductionPolicyError as exc:
        return [str(exc)]
    except Exception as exc:
        return [f"원본 측정 실패: {exc}"]
    return []


def check_cafe(root: Path) -> list[str]:
    problems = []
    manifest_path = root / "cafe/06_cafe_manifest.json"
    local_path = root / "cafe/11_local_validation.json"
    if not manifest_path.is_file():
        return ["06_cafe_manifest.json 없음"]
    if not local_path.is_file():
        return ["11_local_validation.json 없음"]
    local = _read(local_path)
    if local.get("status") != "pass":
        problems.append(f"로컬 검증 status={local.get('status')}")
    problems += [f"로컬 검증 실패: {name}"
                 for name, ok in (local.get("checks") or {}).items() if not ok]
    body = root / "cafe/03_cafe_body.txt"
    if not body.is_file():
        problems.append("03_cafe_body.txt 없음")
    return problems


def check_cardnews(root: Path) -> list[str]:
    from content_lineage import validate_cardnews_origin
    from youtube_cardnews_pipeline import validate_youtube_post

    problems = []
    deck_path = root / "cardnews/04_cardnews_deck.json"
    post_path = root / "cardnews/05_youtube_community_post.txt"
    if not deck_path.is_file():
        problems.append("04_cardnews_deck.json 없음")
    else:
        try:
            validate_cardnews_origin(_read(deck_path))
        except Exception as exc:
            problems.append(f"카드 출처 결속 실패: {str(exc)[:120]}")
    if not post_path.is_file():
        # This file simply did not exist on any run for weeks.
        problems.append("05_youtube_community_post.txt 없음")
    else:
        try:
            validate_youtube_post(post_path.read_text(encoding="utf-8"), source_key=root.name.rsplit('-', 1)[0])
        except Exception as exc:
            problems.append(f"커뮤니티 본문 구조: {str(exc)[:120]}")
    return problems


def check_shorts(root: Path) -> list[str]:
    from content_lineage import validate_shorts_origin

    problems = []
    shorts = root / "shorts"
    script = shorts / "07_script_final.txt"
    if not script.is_file():
        return ["07_script_final.txt 없음"]
    text = script.read_text(encoding="utf-8")
    cta = CTA_PATTERN.search(text)
    if not cta:
        problems.append("고정 CTA 문장 없음")

    # A refused render says so; without this it reads as merely unrendered.
    visual_path = shorts / "visual_validation.json"
    if visual_path.is_file() and _read(visual_path).get("status") == "rejected_still_source":
        still = _read(visual_path)["source_stillness"]
        return [f"원본이 거의 정지해 렌더 거부: {still['largest_identical_group']}/"
                f"{still['checkpoints']} 시점 동일"]
    if not (shorts / "final.mp4").is_file():
        problems.append("final.mp4 없음 (렌더 전)")
        return problems

    machine = shorts / "machine_validation.json"
    if not machine.is_file():
        problems.append("machine_validation.json 없음")
    elif _read(machine).get("status") != "pass":
        problems.append("기계 검증 불합격")

    if not visual_path.is_file():
        problems.append("visual_validation.json 없음")
    else:
        visual = _read(visual_path)
        stillness = visual.get("source_stillness")
        if not stillness:
            problems.append("정지화면 측정 없음 (게이트 이전 렌더)")
        elif stillness["largest_identical_group"] > stillness["limit"]:
            problems.append(
                f"원본이 거의 정지: {stillness['largest_identical_group']}/"
                f"{stillness['checkpoints']} 시점 동일")

    production = shorts / "production_manifest.json"
    if not production.is_file():
        problems.append("production_manifest.json 없음")
    else:
        title = str(_read(production).get("title_candidate") or "")
        if not title or SENTINEL_TITLE.match(title):
            problems.append(f"업로드 제목이 센티넬 그대로: {title or '(비어 있음)'}")
        try:
            validate_shorts_origin(shorts)
        except Exception as exc:
            problems.append(f"쇼츠 출처 결속 실패: {str(exc)[:120]}")
    return problems


def audit(root: Path) -> dict:
    """Every channel's verdict for one produced source."""
    # Some roots under outputs/ are recovery workspaces or runs that never got
    # past their first step. Listing four channel failures for those buries the
    # runs that really are broken.
    if not (root / "cafe/06_cafe_manifest.json").is_file() and not (root / "shorts/final.mp4").is_file():
        return {"root": str(root), "source_key": root.name.rsplit("-", 1)[0],
                "status": "not_a_production", "channels": {}}
    channels = {}
    for name, check in (("source", check_source), ("cafe", check_cafe),
                        ("cardnews", check_cardnews), ("shorts", check_shorts)):
        try:
            channels[name] = check(root)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            channels[name] = [f"검증 입력 오류: {type(exc).__name__}: {str(exc)[:160]}"]
    return {"root": str(root), "source_key": root.name.rsplit("-", 1)[0],
            "status": "pass" if not any(channels.values()) else "fail",
            "channels": {name: {"status": "pass" if not problems else "fail",
                                "problems": problems}
                         for name, problems in channels.items()}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="+", help="outputs/<source>-<date>")
    args = parser.parse_args(argv)
    worst = 0
    for root in args.root:
        result = audit(root.resolve())
        print(json.dumps(result, ensure_ascii=False))
        worst = max(worst, 0 if result["status"] in {"pass", "not_a_production"} else 1)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
