# -*- coding: utf-8 -*-

import json
import os
import tempfile
import unittest
import configparser
from unittest import mock

from publisher_contract import (
    IMAGE_MARKER,
    PublisherContractError,
    REFERENCE_PARAGRAPH_COUNTS,
    SCENE_MARKER,
    assert_cafe_target,
    build_reference_5854_body,
    notification_message,
    reference_body_without_markers,
    validate_reference_5854_body,
    write_result,
)
from tests.reference_5854_fixture import REFERENCE_GROUPS, reference_5854_manuscript
from publisher_notify import send_verified_article
from notebook_cafe_auto import apply_template_options
from youtube_cafe_auto import (
    _cookie_configs,
    _parse_json3,
    verify_published_article,
)


class Reference5854ContractTests(unittest.TestCase):
    def test_reference_template_uses_direct_members_link_instead_of_embed_card(self):
        options = apply_template_options(
            {"source_link_card": True, "source_label": "원본"},
            "reference-5854",
        )

        self.assertFalse(options["source_link_card"])
        self.assertEqual(options["source_label"], "")

    def test_reference_publish_verification_requires_the_exact_direct_href(self):
        exact_url = (
            "https://www.youtube.com/watch?v=4zwr1lRbbcA"
            "&t=2s&pp=0gcJCRMMAYcqIYzv"
        )

        class FakeDriver:
            current_url = "https://cafe.naver.com/westudyssat/5959"
            title = "새 강의 : 네이버 카페"

            def __init__(self, hrefs):
                self.hrefs = hrefs

            def execute_script(self, script):
                if "document.readyState" in script:
                    return "complete"
                if "document.body && document.body.innerText" in script and "var text" not in script:
                    return "새 강의"
                return {
                    "text": "새 강의",
                    "imageCount": 5,
                    "textGroupCount": 6,
                    "quotationCount": 0,
                    "ogLinkCount": 0,
                    "sourceLinkHrefs": self.hrefs,
                    "html": "<html></html>",
                }

        common = {
            "title": "새 강의",
            "source_url": exact_url,
            "min_images": 5,
            "expected_text_groups": 6,
            "expected_og_links": 0,
            "exact_images": True,
            "require_exact_source_link": True,
            "wait": 1,
        }
        wrong = verify_published_article(
            FakeDriver(["https://youtu.be/4zwr1lRbbcA"]), **common)
        correct = verify_published_article(FakeDriver([exact_url]), **common)

        self.assertFalse(wrong["sourceLinkOk"])
        self.assertFalse(wrong["ok"])
        self.assertTrue(correct["sourceLinkOk"])
        self.assertTrue(correct["ok"])

    def test_scene_markers_become_exact_interleaving(self):
        groups = ["\n\n".join(group) for group in REFERENCE_GROUPS]
        manuscript = reference_5854_manuscript()

        body = build_reference_5854_body(manuscript)

        self.assertEqual(body.count(IMAGE_MARKER), 5)
        self.assertNotIn(SCENE_MARKER, body)
        self.assertEqual([part.strip() for part in body.split(IMAGE_MARKER)], groups)
        shape = validate_reference_5854_body(body)
        self.assertEqual(shape["textGroupCount"], 6)
        self.assertEqual(shape["paragraphCounts"], list(REFERENCE_PARAGRAPH_COUNTS))

    def test_marker_free_fallback_preserves_all_paragraph_text(self):
        manuscript = reference_5854_manuscript().replace(SCENE_MARKER, "")

        body = build_reference_5854_body(manuscript)

        self.assertEqual(body.count(IMAGE_MARKER), 5)
        self.assertEqual(
            reference_body_without_markers(body),
            "".join(manuscript.split()),
        )

    def test_partial_scene_marker_set_is_rejected(self):
        manuscript = f"첫 문단\n\n{SCENE_MARKER}\n\n둘째 문단"
        with self.assertRaises(PublisherContractError):
            build_reference_5854_body(manuscript)

    def test_too_few_paragraphs_are_rejected(self):
        manuscript = "\n\n".join(f"문단 {index}" for index in range(1, 6))
        with self.assertRaises(PublisherContractError):
            build_reference_5854_body(manuscript)

    def test_generic_six_group_summary_is_rejected(self):
        manuscript = f"\n\n{SCENE_MARKER}\n\n".join(
            f"본문 구간 {index}" for index in range(1, 7))
        with self.assertRaisesRegex(PublisherContractError, "문단 배열"):
            build_reference_5854_body(manuscript)

    def test_reference_transition_drift_is_rejected(self):
        manuscript = reference_5854_manuscript().replace(
            "여기서 꼭 나오는 질문이 있습니다.",
            "이제 자주 받는 질문을 보겠습니다.",
        )
        with self.assertRaisesRegex(PublisherContractError, "5구간 1문단"):
            build_reference_5854_body(manuscript)

    def test_headings_and_blockquotes_are_rejected(self):
        body = IMAGE_MARKER.join(["## 소제목"] + [f"본문 {i}" for i in range(2, 7)])
        with self.assertRaises(PublisherContractError):
            validate_reference_5854_body(body)

    def test_approved_cafe_board_is_checked_before_write(self):
        actual = assert_cafe_target(
            "https://cafe.naver.com/f-e/cafes/26321967/menus/315",
            "26321967", "315")
        self.assertEqual(actual, {"clubId": "26321967", "menuId": "315"})
        with self.assertRaises(PublisherContractError):
            assert_cafe_target(
                "https://cafe.naver.com/f-e/cafes/26321967/menus/999",
                "26321967", "315")
        with self.assertRaises(PublisherContractError):
            assert_cafe_target(
                "https://cafe.naver.com/f-e/cafes/111/menus/222",
                "111", "222")


class ResultAndNotificationTests(unittest.TestCase):
    def test_notification_has_review_request_and_url(self):
        message = notification_message("새 강의", "https://cafe.naver.com/example/1")
        self.assertIn("한 번 확인 부탁드립니다.", message)
        self.assertTrue(message.endswith("https://cafe.naver.com/example/1"))

    def test_result_file_is_valid_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "nested", "result.json")
            written = write_result(path, {"ok": True, "status": "dry-run-complete"})
            with open(path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            self.assertEqual(loaded, written)
            self.assertIn("updatedAt", loaded)

    def test_telegram_request_is_only_built_from_enabled_local_config(self):
        config = configparser.ConfigParser()
        config["PUBLISH_NOTIFY"] = {
            "enabled": "true",
            "telegram_token": "test-token",
            "telegram_chat_id": "-100123",
            "telegram_thread_id": "77",
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"ok": true, "result": {"message_id": 42}}'

        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()) as call:
            result = send_verified_article(
                config, "새 강의", "https://cafe.naver.com/example/1")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["messageId"], 42)
        request = call.call_args.args[0]
        self.assertIn(b"message_thread_id=77", request.data)
        self.assertIn(b"chat_id=-100123", request.data)

    def test_json3_caption_parser_keeps_text_and_deduplicates_adjacent_events(self):
        payload = {
            "events": [
                {"segs": [{"utf8": "첫 문장"}]},
                {"segs": [{"utf8": "첫 문장"}]},
                {"segs": [{"utf8": "둘째"}, {"utf8": " 문장"}]},
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "caption.json3")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            self.assertEqual(_parse_json3(path), "첫 문장 둘째 문장")

    def test_public_youtube_path_is_tried_before_browser_cookie_profiles(self):
        with mock.patch("youtube_cafe_auto.os.path.exists", return_value=False):
            labels = [label for label, _options in _cookie_configs()]
        self.assertEqual(labels, ["none", "chrome", "edge", "firefox"])


if __name__ == "__main__":
    unittest.main()
