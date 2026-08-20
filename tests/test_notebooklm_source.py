# -*- coding: utf-8 -*-

import unittest
from unittest import mock

import notebooklm_source as nlm


class NotebookLMTemplateTests(unittest.TestCase):
    def test_reference_template_uses_scene_prompt_and_disables_heading_retry(self):
        manuscript = "\n\n[[SCENE]]\n\n".join(f"구간 {i}" for i in range(1, 7))
        fake_fetch = mock.AsyncMock(return_value=(manuscript, "notebook-id"))
        cfg = {
            "scope_to_new_source": True,
            "source_wait_timeout": 300,
            "strip_promo": False,
        }

        with mock.patch.object(nlm, "_fetch_async", fake_fetch):
            result = nlm.fetch_manuscript(
                "https://youtu.be/abcdefghijk", cfg,
                log=lambda _message: None,
                template="reference-5854",
            )

        self.assertEqual(result, manuscript)
        call = fake_fetch.call_args.args
        self.assertEqual(call[1], nlm.REFERENCE_5854_PROMPT)
        self.assertEqual(call[8], False)

    def test_reference_prompt_requests_exact_scene_marker_count(self):
        self.assertIn("정확히 6개의 텍스트 구간", nlm.REFERENCE_5854_PROMPT)
        self.assertIn("정확히 5개", nlm.REFERENCE_5854_PROMPT)
        self.assertIn("영상에 없는 내용은", nlm.REFERENCE_5854_PROMPT)


if __name__ == "__main__":
    unittest.main()
