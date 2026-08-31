import unittest
from unittest import mock

import comment_bot
import youtube_cafe_auto


class AsideIntegrationDispatchTests(unittest.TestCase):
    def test_comment_bot_article_read_uses_aside(self):
        driver = comment_bot.AsideDriver()
        items = [{"id": "12", "title": "가입인사", "cells": []}]
        with mock.patch.object(comment_bot, "aside_fetch_articles", return_value=items) as fetch:
            self.assertEqual(
                comment_bot.fetch_article_ids(driver, "https://example.invalid/board"),
                [("12", "가입인사")],
            )
        fetch.assert_called_once()

    def test_comment_bot_write_uses_aside_without_selenium_driver(self):
        driver = comment_bot.AsideDriver()
        with (
            mock.patch.object(
                comment_bot, "aside_write_comment",
                return_value={"status": "ok", "ok": True, "reason": "ready"},
            ) as write,
            mock.patch.object(comment_bot, "_rand_delay"),
        ):
            ok = comment_bot.write_comment(
                driver, "1", "2", "환영합니다", "https://example.invalid/board", (0, 0),
                dry_run=True,
            )
        self.assertTrue(ok)
        self.assertTrue(write.call_args.kwargs["dry_run"])

    def test_cafe_publisher_dispatches_to_aside(self):
        youtube_cafe_auto.CAFE_URL = "https://cafe.naver.com/f-e/cafes/1/menus/2"
        with mock.patch.object(
            youtube_cafe_auto,
            "post_to_naver_cafe_aside",
            return_value={"status": "published", "url": "https://example.invalid/post"},
        ) as publish:
            result = youtube_cafe_auto.post_to_naver_cafe(
                "제목", "본문", [], {"browser_backend": "aside", "cta_enabled": False}
            )
        self.assertEqual(result["status"], "published")
        self.assertTrue(publish.call_args.kwargs["publish"])


if __name__ == "__main__":
    unittest.main()

