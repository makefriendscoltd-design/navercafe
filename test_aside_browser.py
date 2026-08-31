import base64
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aside_browser


class AsideBrowserUnitTests(unittest.TestCase):
    def test_parse_result_uses_last_marker(self):
        output = (
            "human status\n"
            f"{aside_browser.RESULT_MARKER}{json.dumps({'status': 'ok', 'value': 1})}\n"
        )
        self.assertEqual(aside_browser._parse_result(output)["value"], 1)

    def test_parse_result_turns_login_into_specific_error(self):
        output = aside_browser.RESULT_MARKER + json.dumps(
            {"status": "login_required", "site": "naver"}
        )
        with self.assertRaises(aside_browser.AsideLoginRequired):
            aside_browser._parse_result(output)

    def test_body_markers_are_removed_but_image_boundaries_remain(self):
        body = "[BOLD]첫 문단[/BOLD]\n[BLOCKQUOTE]소제목[/BLOCKQUOTE][IMAGE_HERE]끝"
        self.assertEqual(
            aside_browser._plain_body_chunks(body),
            ["첫 문단\n\n소제목", "끝"],
        )

    def test_cafe_footer_keeps_study_cta_before_separate_source_link(self):
        body = aside_browser._compose_naver_body(
            "본문",
            cta_text="AI 오프라인 스터디를 진행합니다.\n관심 있으면 이 글을 읽어보세요.",
            cta_link_url="https://cafe.naver.com/westudyssat/4188",
            source_label="▶ 원본 영상",
            source_url="https://youtu.be/example",
        )
        self.assertEqual(
            body,
            "본문\n\n\n"
            "AI 오프라인 스터디를 진행합니다.\n관심 있으면 이 글을 읽어보세요.\n\n"
            "https://cafe.naver.com/westudyssat/4188\n\n\n"
            "▶ 원본 영상\nhttps://youtu.be/example",
        )

    @mock.patch("aside_browser.run_repl")
    def test_cafe_urls_are_reserved_for_rich_card_paste(self, run_repl):
        run_repl.return_value = {"status": "filled"}
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "frame.jpg"
            image.write_bytes(b"jpg")
            aside_browser.post_to_naver_cafe(
                "제목",
                "[BLOCKQUOTE]소제목[/BLOCKQUOTE]\n본문\n[IMAGE_HERE]",
                [image],
                cafe_url="https://cafe.naver.com/ca-fe/cafes/1/menus/2/articles/write",
                cta_text="스터디 안내",
                cta_link_url="https://cafe.naver.com/westudyssat/4188",
                source_url="https://youtu.be/example",
                publish=False,
            )

        code = run_repl.call_args.args[0]
        encoded = re.search(r"atob\('([^']+)'\)", code).group(1)
        payload = json.loads(base64.b64decode(encoded))
        self.assertNotIn("https://", payload["body"])
        self.assertEqual(payload["ctaLinkUrl"], "https://cafe.naver.com/westudyssat/4188")
        self.assertEqual(payload["sourceUrl"], "https://youtu.be/example")
        self.assertEqual(
            payload["expectedSequence"],
            ["quote", "text", "image", "text", "text"],
        )

    @mock.patch("aside_browser.run_repl")
    def test_cafe_draft_clicks_real_temporary_registration(self, run_repl):
        run_repl.return_value = {"status": "draft_saved", "saved_time": "방금"}
        aside_browser.post_to_naver_cafe(
            "예약 제목",
            "본문",
            [],
            cafe_url="https://cafe.naver.com/ca-fe/cafes/1/menus/2/articles/write",
            publish=False,
            save_draft=True,
        )

        code = run_repl.call_args.args[0]
        encoded = re.search(r"atob\('([^']+)'\)", code).group(1)
        payload = json.loads(base64.b64decode(encoded))
        self.assertTrue(payload["saveDraft"])
        self.assertFalse(payload["publish"])
        self.assertIn(".btn_temp_save", code)
        self.assertIn(".temp_item_title", code)

    def test_cafe_cannot_save_and_publish_at_once(self):
        with self.assertRaises(ValueError):
            aside_browser.post_to_naver_cafe(
                "제목", "본문", [], cafe_url="https://example.invalid",
                publish=True, save_draft=True,
            )

    @mock.patch("aside_browser.run_repl")
    def test_saved_draft_publish_requires_exact_title_and_structure(self, run_repl):
        run_repl.return_value = {"status": "published", "url": "https://cafe.naver.com/x/1"}
        result = aside_browser.publish_saved_naver_cafe_draft(
            "예약 제목",
            cafe_url="https://cafe.naver.com/f-e/cafes/1/menus/2",
            board_name="AI 자동화&수익화 정보",
            expected_images=3,
            expected_quotes=2,
            expected_sequence=["quote", "text", "image"],
            expected_quote_texts=["첫 제목", "둘째 제목"],
            cta_link_url="https://cafe.naver.com/westudyssat/4188",
            source_url="https://youtu.be/example",
        )

        self.assertEqual(result["status"], "published")
        code = run_repl.call_args.args[0]
        self.assertIn("exact.length!==1", code)
        self.assertIn("본문 컴포넌트 순서 불일치", code)
        self.assertIn("YouTube 미리보기 카드 누락", code)

    def test_staged_uploads_copy_only_requested_files(self):
        with tempfile.TemporaryDirectory() as source_dir:
            source = Path(source_dir) / "sample.png"
            source.write_bytes(b"png")
            with aside_browser.staged_uploads([source]) as (root, names):
                self.assertEqual(names, ["upload-01.png"])
                self.assertEqual((root / names[0]).read_bytes(), b"png")
            self.assertFalse(root.exists())

    @mock.patch("aside_browser.subprocess.run")
    @mock.patch("aside_browser.resolve_aside_cli", return_value="/tmp/aside")
    def test_run_repl_passes_account_without_shell(self, _resolve, run):
        run.return_value = mock.Mock(
            returncode=0,
            stdout=aside_browser.RESULT_MARKER + '{"status":"ok"}\n',
            stderr="",
        )
        result = aside_browser.run_repl("emit({status:'ok'})", account="u0")
        self.assertEqual(result["status"], "ok")
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["/tmp/aside", "repl", "--account", "u0"])
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    @mock.patch("shorts_video.validate_upload_ready", return_value={"status": "pass"})
    @mock.patch("aside_browser.run_repl")
    def test_short_upload_embeds_nonzero_mp4_and_requires_provider_id(self, run_repl, _gate):
        run_repl.return_value = {
            "status": "published",
            "video_id": "abcdefghijk",
            "url": "https://www.youtube.com/shorts/abcdefghijk",
        }
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "short.mp4"
            video.write_bytes(b"nonzero-mp4")
            result = aside_browser.upload_youtube_short(
                video,
                "제목 #Shorts",
                "설명",
                publish=True,
                expected_channel="나민수 AI",
            )

        self.assertEqual(result["video_id"], "abcdefghijk")
        code = run_repl.call_args.args[0]
        encoded = re.search(r"atob\('([^']+)'\)", code).group(1)
        payload = json.loads(base64.b64decode(encoded))
        self.assertEqual(payload["expected"], "나민수 AI")
        self.assertEqual(payload["title"], "제목")
        self.assertEqual(base64.b64decode(payload["video"]["base64"]), b"nonzero-mp4")
        self.assertEqual(payload["video"]["mimeType"], "video/mp4")
        self.assertIn('tp-yt-paper-radio-button[name="PUBLIC"]', code)
        self.assertIn("state.row.includes('공개')", code)
        self.assertIn("state.row.includes('게시됨')", code)

    def test_short_publish_requires_expected_channel(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "short.mp4"
            video.write_bytes(b"mp4")
            with mock.patch("shorts_video.validate_upload_ready", return_value={}):
                with self.assertRaises(ValueError):
                    aside_browser.upload_youtube_short(video, "제목", publish=True)

    @mock.patch("shorts_video.validate_upload_ready", side_effect=RuntimeError("게이트 실패"))
    @mock.patch("aside_browser.run_repl")
    def test_short_upload_stops_before_provider_when_policy_fails(self, run_repl, _gate):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "short.mp4"
            video.write_bytes(b"mp4")
            with self.assertRaisesRegex(RuntimeError, "게이트 실패"):
                aside_browser.upload_youtube_short(
                    video, "제목", publish=True, expected_channel="나민수 AI"
                )
        run_repl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
