# -*- coding: utf-8 -*-

import asyncio
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

import notebooklm_source as nlm
from tests.reference_5854_fixture import reference_5854_manuscript


class NotebookLMTemplateTests(unittest.TestCase):
    def test_reference_template_uses_scene_prompt_and_disables_heading_retry(self):
        manuscript = reference_5854_manuscript()
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
        self.assertTrue(fake_fetch.call_args.kwargs["reference_template"])

    def test_reference_prompt_requests_exact_scene_marker_count(self):
        self.assertIn("정확히 6개의 텍스트 구간", nlm.REFERENCE_5854_PROMPT)
        self.assertIn("정확히 5개", nlm.REFERENCE_5854_PROMPT)
        self.assertIn("영상에 없는 내용은", nlm.REFERENCE_5854_PROMPT)
        self.assertIn("Qwen3-TTS", nlm.REFERENCE_5854_PROMPT)
        self.assertIn("후기 보상", nlm.REFERENCE_5854_PROMPT)
        self.assertIn("4개, 8개, 11개, 8개, 6개, 4개", nlm.REFERENCE_5854_PROMPT)
        self.assertIn("이게 말이 됩니까?", nlm.REFERENCE_5854_PROMPT)
        self.assertIn("여기서 꼭 나오는 질문이 있습니다.", nlm.REFERENCE_5854_PROMPT)

    def test_generic_six_group_summary_is_rejected_as_reference_drift(self):
        manuscript = "\n\n[[SCENE]]\n\n".join(f"구간 {i}" for i in range(1, 7))

        problem = nlm._reference_5854_problem(manuscript)

        self.assertIn("문단 배열", problem)

    def test_reference_exact_rhetoric_and_markdown_are_normalized(self):
        manuscript = reference_5854_manuscript()
        drifted = manuscript.replace(
            "이게 말이 됩니까?",
            "**이게 정말 말이 되는 일입니까?**",
            1,
        ).replace(
            "여기서 꼭 나오는 질문이 있습니다.",
            "> 이제 자주 받는 질문을 보겠습니다.",
        )

        normalized = nlm._normalize_reference_5854(
            drifted, log=lambda _message: None)

        self.assertEqual(normalized, manuscript)
        self.assertEqual(nlm._reference_5854_problem(normalized), "")

    def test_known_caption_terms_are_normalized(self):
        raw = "맥패밀리의 다민수 대표가 QN3와 복스 스타일을 소개하고 정립금을 안내했습니다."
        result = nlm._normalize_known_terms(raw, log=lambda _message: None)

        self.assertEqual(
            result,
            "메이크패밀리의 나민수 대표가 Qwen3-TTS와 VOX 스타일을 소개하고 적립금을 안내했습니다.",
        )

    def test_reference_promo_pricing_and_rewards_are_removed(self):
        text = (
            "PDF 원고를 전자책으로 조판하고 판매 상세 페이지를 만드는 시스템을 공개했습니다.\n\n"
            "후기 작성자에게 노션 자료를 무료로 제공합니다. "
            "전자책을 9만 9천 원에 선착순 판매합니다. "
            "네이버 카페 후기에는 적립금 1만 원을 드립니다. "
            "수강생은 적립금 혜택으로 교육을 이어갈 수 있습니다."
        )

        result = nlm.strip_promo_tail(text, log=lambda _message: None)

        self.assertEqual(
            result,
            "PDF 원고를 전자책으로 조판하고 판매 상세 페이지를 만드는 시스템을 공개했습니다.",
        )

    def test_missing_scene_marker_is_repaired_without_changing_text(self):
        sections = [
            "첫 문장입니다. 둘째 문장도 있습니다.",
            "세 번째 구간입니다.",
            "네 번째 구간입니다.",
            "다섯 번째 구간입니다.",
            "여섯 번째가 빠진 출력입니다.",
        ]
        raw = "\n\n[[SCENE]]\n\n".join(sections)

        result = nlm._ensure_scene_markers(raw, 5, log=lambda _message: None)

        self.assertEqual(result.count("[[SCENE]]"), 5)
        self.assertEqual(nlm._text_signature(result), nlm._text_signature(raw))
        self.assertEqual(len(result.split("[[SCENE]]")), 6)

    def test_text_fallback_passes_transcript_as_notebook_source(self):
        manuscript = reference_5854_manuscript()
        fake_fetch = mock.AsyncMock(return_value=(manuscript, "notebook-id"))
        cfg = {"strip_promo": False}

        with mock.patch.object(nlm, "_fetch_async", fake_fetch):
            result = nlm.fetch_manuscript_from_text(
                "https://youtu.be/abcdefghijk", "자동자막 원문", cfg,
                log=lambda _message: None,
                template="reference-5854",
            )

        self.assertEqual(result, manuscript)
        self.assertEqual(fake_fetch.call_args.kwargs["source_text"], "자동자막 원문")

    def test_reference_drift_is_retried_once_inside_same_notebook(self):
        calls = []
        valid = reference_5854_manuscript()
        invalid = "\n\n[[SCENE]]\n\n".join(f"요약 {i}" for i in range(1, 7))

        class FakeNotebooks:
            async def create(self, _title):
                return SimpleNamespace(id="temporary-notebook", title="temporary")

            async def delete(self, _notebook_id):
                return None

        class FakeSources:
            async def add_text(self, *_args, **_kwargs):
                return SimpleNamespace(id="source-id", title="transcript")

        class FakeChat:
            async def ask(self, _notebook_id, prompt, **_kwargs):
                calls.append(prompt)
                return SimpleNamespace(answer=invalid if len(calls) == 1 else valid)

        class FakeClient:
            auth = SimpleNamespace(account_email=None)

            def __init__(self):
                self.notebooks = FakeNotebooks()
                self.sources = FakeSources()
                self.chat = FakeChat()

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
            answer, _ = asyncio.run(nlm._fetch_async(
                "https://youtu.be/abcdefghijk", nlm.REFERENCE_5854_PROMPT,
                "", "[자동]", True, 300, True, False, False, "",
                lambda _message: None, source_text="자동자막",
                reference_template=True,
            ))

        self.assertEqual(answer, valid)
        self.assertEqual(len(calls), 2)
        self.assertIn("직전 출력은 5854 고정 골격 검증", calls[1])

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
