# -*- coding: utf-8 -*-

import asyncio
import sys
import types
import unittest
from types import SimpleNamespace
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

    def test_temporary_notebook_is_deleted_when_source_add_fails(self):
        deleted = []

        class FakeNotebooks:
            async def create(self, _title):
                return SimpleNamespace(id="temporary-notebook", title="temporary")

            async def delete(self, notebook_id):
                deleted.append(notebook_id)

        class FakeSources:
            async def list(self, _notebook_id):
                return []

            async def add_url(self, *_args, **_kwargs):
                raise RuntimeError("source rejected")

        class FakeClient:
            auth = SimpleNamespace(account_email=None)

            def __init__(self):
                self.notebooks = FakeNotebooks()
                self.sources = FakeSources()
                self.chat = SimpleNamespace()

            @classmethod
            def from_storage(cls, **_kwargs):
                return cls()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        fake_package = types.ModuleType("notebooklm")
        fake_package.NotebookLMClient = FakeClient
        fake_package.SourceStatus = SimpleNamespace(READY="ready")
        fake_exceptions = types.ModuleType("notebooklm.exceptions")
        fake_exceptions.AuthError = type("AuthError", (Exception,), {})
        fake_exceptions.NotebookNotFoundError = type(
            "NotebookNotFoundError", (Exception,), {})

        with mock.patch.dict(sys.modules, {
            "notebooklm": fake_package,
            "notebooklm.exceptions": fake_exceptions,
        }):
            with self.assertRaisesRegex(RuntimeError, "source rejected"):
                asyncio.run(nlm._fetch_async(
                    "https://youtu.be/abcdefghijk", "prompt", "", "[자동]",
                    True, 300, True, False, False, "", lambda _message: None,
                ))

        self.assertEqual(deleted, ["temporary-notebook"])


if __name__ == "__main__":
    unittest.main()
