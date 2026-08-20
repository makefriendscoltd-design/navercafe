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
    SCENE_MARKER,
    assert_cafe_target,
    build_reference_5854_body,
    notification_message,
    reference_body_without_markers,
    validate_reference_5854_body,
    write_result,
)
from publisher_notify import send_verified_article
from youtube_cafe_auto import _parse_json3


class Reference5854ContractTests(unittest.TestCase):
    def test_scene_markers_become_exact_interleaving(self):
        groups = [f"본문 구간 {index}" for index in range(1, 7)]
        manuscript = f"\n\n{SCENE_MARKER}\n\n".join(groups)

        body = build_reference_5854_body(manuscript)

        self.assertEqual(body.count(IMAGE_MARKER), 5)
        self.assertNotIn(SCENE_MARKER, body)
        self.assertEqual([part.strip() for part in body.split(IMAGE_MARKER)], groups)
        self.assertEqual(validate_reference_5854_body(body)["textGroupCount"], 6)

    def test_marker_free_fallback_preserves_all_paragraph_text(self):
        manuscript = "\n\n".join(f"문단 {index}" for index in range(1, 10))

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


if __name__ == "__main__":
    unittest.main()
