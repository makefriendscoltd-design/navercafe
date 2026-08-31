"""Approve and publish a fully editor-verified Cafe + YouTube bundle."""

from __future__ import annotations

import argparse
import json
import re
import secrets
from pathlib import Path

from PIL import Image

from aside_browser import _compose_naver_body, upload_youtube_short
import notebook_cafe_auto as cafe
from notebooklm_shorts import require_strong_hook, validate_head_copy_connection
import youtube_cafe_auto as auto
from external_publish_tracking import track_external_event, track_publication
from telegram_publish_approval import (
    TelegramApproval,
    load_approval_credentials,
    request_publish_approval,
)
from youtube_cardnews_pipeline import (
    FIRST_PUBLISH_STATE_FILE,
    deck_validation_issues,
    load_runtime_config,
    publish_fingerprint,
    save_first_publish_state,
)
from youtube_community_auto import post_to_youtube_community
from shorts_video import normalize_shorts_title, validate_minsoo_voice_artifact, validate_short_video


SHORTS_ENDING_RE = re.compile(
    r"\d+분 짜리 영상 내용을 모두 정리했습니다\.\s*"
    r"이 자료 궁금하신 분들은 채널을 구독후 프로필 링크를 확인하세요\.$"
)


def load_bundle(root: Path, source_url: str) -> dict:
    root = root.expanduser().resolve()
    title = (root / "02_cafe_title.txt").read_text(encoding="utf-8").strip()
    manuscript = (root / "01d_editor_verified_manuscript.md").read_text(encoding="utf-8").strip()
    cafe_body = (root / "03_verified_cafe_body.txt").read_text(encoding="utf-8").strip()
    youtube_body = (root / "05_youtube_community_post.txt").read_text(encoding="utf-8").strip()
    shorts_body = (root / "07_shorts_script_through_fifth.txt").read_text(encoding="utf-8").strip()
    shorts_video = root / "09_shorts_final.mp4"
    if not shorts_video.is_file():
        rendered = sorted(root.glob("*_shorts_*1080x1920.mp4"))
        if rendered:
            shorts_video = rendered[-1]
    shorts_upload = json.loads((root / "10_shorts_upload.json").read_text(encoding="utf-8"))
    deck = json.loads((root / "04_cardnews_deck.json").read_text(encoding="utf-8"))
    cafe_images = sorted((root / "youtube_frames").glob("youtube-frame-*.jpg"))[
        : cafe_body.count("[IMAGE_HERE]")
    ]
    card_images = sorted((root / "cardnews_png").glob("*.png"))
    return {
        "root": root,
        "source_url": source_url,
        "title": title,
        "manuscript": manuscript,
        "cafe_body": cafe_body,
        "youtube_body": youtube_body,
        "shorts_body": shorts_body,
        "shorts_video": shorts_video,
        "shorts_upload": shorts_upload,
        "deck": deck,
        "cafe_images": cafe_images,
        "card_images": card_images,
    }


def validate_bundle(bundle: dict, optional_config: dict) -> dict:
    errors = []
    if optional_config.get("board_name") != "AI 자동화&수익화 정보":
        errors.append("네이버 카페 게시판 설정이 다릅니다.")
    intact, detail = cafe.verify_manuscript_intact(
        bundle["manuscript"], bundle["cafe_body"]
    )
    if not intact:
        errors.append("카페 원고 무손실 검증 실패: " + detail)
    expected_images = bundle["cafe_body"].count("[IMAGE_HERE]")
    if expected_images != len(bundle["cafe_images"]):
        errors.append(
            f"카페 이미지 수 불일치: 마커 {expected_images}, 파일 {len(bundle['cafe_images'])}"
        )
    if bundle["cafe_body"].count("[BLOCKQUOTE]") != 4:
        errors.append("카페 인용구가 4개가 아닙니다.")
    if not optional_config.get("cta_enabled"):
        errors.append("네이버 카페 오프라인 스터디 CTA가 꺼져 있습니다.")
    if optional_config.get("cta_text") != auto.DEFAULT_CAFE_CTA_TEXT:
        errors.append("네이버 카페 오프라인 스터디 CTA 문구가 다릅니다.")
    if optional_config.get("cta_link_url") != auto.DEFAULT_CAFE_CTA_URL:
        errors.append("네이버 카페 패밀리데이 모집 공지 링크가 다릅니다.")
    if not SHORTS_ENDING_RE.search(bundle["shorts_body"]):
        errors.append("쇼츠 고정 CTA가 다릅니다.")
    try:
        require_strong_hook(bundle["shorts_body"])
    except RuntimeError as exc:
        errors.append(str(exc))
    if "여섯째" in bundle["shorts_body"] or "6. " in bundle["shorts_body"]:
        errors.append("쇼츠에 6번째 이후 내용이 남았습니다.")
    shorts_title = str(bundle["shorts_upload"].get("title") or "").strip()
    try:
        if shorts_title != normalize_shorts_title(shorts_title):
            errors.append("쇼츠 제목 끝에 해시태그가 남았습니다.")
    except RuntimeError as exc:
        errors.append(str(exc))
    try:
        validate_head_copy_connection(
            str(bundle["shorts_upload"].get("headline") or ""),
            bundle["shorts_body"],
        )
    except RuntimeError as exc:
        errors.append(str(exc))
    try:
        shorts_validation = validate_short_video(bundle["shorts_video"])
        shorts_validation["voice"] = validate_minsoo_voice_artifact(bundle["shorts_video"])
    except RuntimeError as exc:
        errors.append(str(exc))
        shorts_validation = {}
    deck_issues = deck_validation_issues(bundle["deck"])
    errors.extend(deck_issues)
    if len(bundle["card_images"]) != 10:
        errors.append(f"카드뉴스 PNG가 {len(bundle['card_images'])}장입니다.")
    for path in bundle["card_images"]:
        with Image.open(path) as image:
            if image.size != (1080, 1080):
                errors.append(f"카드 크기 오류: {path.name}={image.size}")
    if errors:
        raise RuntimeError(" | ".join(errors))
    return {
        "title": bundle["title"],
        "board": optional_config.get("board_name"),
        "cafe_images": len(bundle["cafe_images"]),
        "cards": len(bundle["card_images"]),
        "quotes": bundle["cafe_body"].count("[BLOCKQUOTE]"),
        "bold": bundle["cafe_body"].count("[BOLD]"),
        "highlight": bundle["cafe_body"].count("[HIGHLIGHT]"),
        "shorts_ending": True,
        "shorts_title_hashtag_free": True,
        "shorts_headline": bundle["shorts_upload"].get("headline", ""),
        "shorts_video": shorts_validation,
    }


def compose_cafe_review_body(bundle: dict, optional_config: dict) -> str:
    """Show Telegram reviewers the exact CTA and source footer that will publish."""
    return _compose_naver_body(
        bundle["cafe_body"],
        cta_text=optional_config.get("cta_text", "")
        if optional_config.get("cta_enabled") else "",
        cta_link_text=optional_config.get("cta_link_text", "")
        if optional_config.get("cta_enabled") else "",
        cta_link_url=optional_config.get("cta_link_url", "")
        if optional_config.get("cta_enabled") else "",
        source_label=optional_config.get("source_label") or "▶ 원본 영상",
        source_url=bundle["source_url"],
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--approval-only",
        action="store_true",
        help="완성본은 이미 전송된 경우 새 승인 버튼만 보내고 이어서 발행",
    )
    args = parser.parse_args(argv)

    config, optional_config, _ = load_runtime_config()
    bundle = load_bundle(Path(args.root), args.source_url)
    validation = validate_bundle(bundle, optional_config)
    print(json.dumps({"status": "validated", **validation}, ensure_ascii=False), flush=True)
    if args.validate_only:
        return 0

    if args.approval_only:
        credentials = load_approval_credentials(config)
        client = TelegramApproval(str(credentials["token"]), str(credentials["chat_id"]))
        client.verify_destination(
            expected_bot=str(credentials["expected_bot"]),
            expected_chat_type=str(credentials["expected_chat_type"]),
        )
        after_update_id = client.latest_update_id()
        sent_count = client.send_text(
            "[승인 버튼 재발급]\n앞서 전송한 완성본을 확인한 뒤 아래 새 버튼을 눌러주세요."
        )
        nonce = secrets.token_urlsafe(8)
        sent_count += client.send_approval_buttons(nonce)
        track_external_event(
            "telegram",
            args.source_url,
            campaign="youtube-content-repurpose-approval-button-reissue",
            stage="sent",
            count=sent_count,
        )
        approved = client.wait_for_decision(nonce, after_update_id=after_update_id)
    else:
        approved = request_publish_approval(
            config,
            source_url=args.source_url,
            cafe_title=bundle["title"],
            cafe_body=compose_cafe_review_body(bundle, optional_config),
            youtube_body=bundle["youtube_body"],
            card_images=[str(path) for path in bundle["card_images"]],
            factcheck_status="editor_verified",
            shorts_body=bundle["shorts_body"],
        )
    if not approved:
        print(json.dumps({"status": "held"}, ensure_ascii=False), flush=True)
        return 2

    fingerprint = publish_fingerprint(
        args.source_url,
        bundle["title"],
        bundle["cafe_body"],
        bundle["youtube_body"],
        bundle["shorts_body"],
    )
    state = {
        "approved_fingerprint": fingerprint,
        "source_id": auto._publish_source_key(args.source_url),
        "cafe_published": False,
        "youtube_published": False,
        "shorts_published": False,
        "first_review_completed": False,
    }
    save_first_publish_state(state)

    cafe_result = auto.post_to_naver_cafe(
        bundle["title"],
        bundle["cafe_body"],
        [str(path) for path in bundle["cafe_images"]],
        optional_config,
        source_url=args.source_url,
        draft=False,
    )
    if cafe_result.get("status") != "published":
        raise RuntimeError(f"네이버 카페 발행 확인 실패: {cafe_result}")
    track_publication("naver_cafe", auto._publish_source_key(args.source_url))
    state["cafe_published"] = True
    state["cafe_source_id"] = state["source_id"]
    state["cafe_url"] = cafe_result.get("url", "")
    save_first_publish_state(state)

    youtube_result = post_to_youtube_community(
        bundle["youtube_body"],
        [str(path) for path in bundle["card_images"]],
        publish=True,
        expected_channel="나민수 AI",
    )
    if youtube_result.get("status") != "published":
        raise RuntimeError(f"YouTube 발행 확인 실패: {youtube_result}")
    track_publication("youtube_community", auto._publish_source_key(args.source_url))
    state["youtube_published"] = True
    state["youtube_source_id"] = state["source_id"]
    state["youtube_url"] = youtube_result.get("url", "")
    save_first_publish_state(state)

    shorts_result = upload_youtube_short(
        bundle["shorts_video"],
        bundle["shorts_upload"].get("title") or (bundle["title"][:88] + " #Shorts")[:100],
        bundle["shorts_upload"].get("description") or "",
        publish=True,
        expected_channel="나민수 AI",
    )
    if shorts_result.get("status") != "published" or not shorts_result.get("video_id"):
        raise RuntimeError(f"YouTube Shorts 발행 확인 실패: {shorts_result}")
    track_publication("youtube_shorts", auto._publish_source_key(args.source_url))
    state["shorts_published"] = True
    state["shorts_source_id"] = state["source_id"]
    state["shorts_url"] = shorts_result.get("url", "")
    state["first_review_completed"] = True
    save_first_publish_state(state)
    print(
        json.dumps(
            {
                "status": "published",
                "cafe": cafe_result,
                "youtube": youtube_result,
                "shorts": shorts_result,
                "state": str(FIRST_PUBLISH_STATE_FILE),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
