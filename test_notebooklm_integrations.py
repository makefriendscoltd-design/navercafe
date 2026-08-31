import configparser
from unittest import mock

import notebooklm_source
import notebooklm_shorts
import youtube_cafe_auto
import youtube_cardnews_pipeline


def test_notebooklm_defaults_to_named_cafe_notebook():
    cfg = notebooklm_source.load_config(configparser.ConfigParser())
    assert cfg["notebook_title"] == "민수대표님_카페글"
    assert cfg["scope_to_new_source"] is True


def test_shorts_notebook_config_is_pinned_to_existing_minsoo_notebook():
    cfg, _ = notebooklm_shorts.load_shorts_config()
    assert cfg["aside_account"] == "u0"
    assert cfg["notebook_title"] == "민수대표님_숏폼"
    assert cfg["notebook_id"] == "ed70fc3b-474b-423a-9ca8-d19934703f27"


def test_naver_draft_uses_aside_without_publishing():
    youtube_cafe_auto.CAFE_URL = "https://cafe.naver.com/f-e/cafes/1/menus/2"
    with mock.patch.object(
        youtube_cafe_auto,
        "post_to_naver_cafe_aside",
        return_value={"status": "draft_saved", "images": 1},
    ) as publish:
        result = youtube_cafe_auto.post_to_naver_cafe(
            "제목",
            "본문",
            ["image.jpg"],
            {"browser_backend": "aside", "cta_enabled": False},
            source_url="https://youtu.be/KUeW3zzF49A?si=tracking",
            draft=True,
        )

    assert result["status"] == "draft_saved"
    assert publish.call_args.kwargs["publish"] is False
    assert publish.call_args.kwargs["save_draft"] is True


def test_source_key_drops_tracking_query():
    assert youtube_cafe_auto._publish_source_key(
        "https://youtu.be/KUeW3zzF49A?si=tracking"
    ) == "youtube:KUeW3zzF49A"


def test_browser_frame_fallback_prefers_aside(tmp_path):
    expected = [str(tmp_path / "frame.jpg")]
    with mock.patch.object(youtube_cardnews_pipeline, "aside_available", return_value=True), mock.patch.object(
        youtube_cardnews_pipeline,
        "capture_youtube_frames_aside",
        return_value=expected,
    ) as capture:
        result = youtube_cardnews_pipeline.capture_youtube_frames_from_browser(
            "https://youtu.be/KUeW3zzF49A", 1, tmp_path
        )

    assert result == expected
    capture.assert_called_once()


def test_cafe_images_are_capped_to_sections_and_cached(tmp_path):
    frame_dir = tmp_path / "youtube_frames"
    frame_dir.mkdir()
    for index in range(1, 11):
        (frame_dir / f"youtube-frame-{index:02d}.jpg").write_bytes(b"image")
    manuscript = "\n\n".join(
        f"## 섹션 {index}\n\n완성된 본문 문장입니다." for index in range(1, 5)
    )
    with (
        mock.patch.object(youtube_cardnews_pipeline.auto, "extract_frames") as extract,
        mock.patch.object(youtube_cardnews_pipeline, "build_body", return_value="본문") as body,
    ):
        _, _, images = youtube_cardnews_pipeline.build_cafe_assets(
            manuscript,
            "https://youtu.be/KUeW3zzF49A",
            "제목",
            10,
            {},
            out_dir=tmp_path,
        )
    assert len(images) == 4
    extract.assert_not_called()
    assert body.call_args.args[1] == 4
