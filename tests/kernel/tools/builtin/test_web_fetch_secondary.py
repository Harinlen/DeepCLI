"""WebFetch secondary-model post-processing (CC parity)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from kernel.tools.builtin.web_fetch import WebFetchTool, _make_secondary_model_prompt
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache


def _ctx(tmp_path: Path, summarise: Any = None) -> ToolContext:
    return ToolContext(
        session_id="s",
        agent_depth=0,
        agent_id=None,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
        summarise=summarise,
    )


class _FakeResult:
    def __init__(self, url: str, content: str, error: str | None = None) -> None:
        self.url = url
        self.content = content
        self.content_type = "text/markdown"
        self.status_code = 200
        self.error = error


class TestWebFetchSecondaryModel:
    @pytest.mark.asyncio
    async def test_post_processing_invoked_when_prompt_and_summarise_available(
        self, tmp_path: Path
    ) -> None:
        """prompt + ctx.summarise -> WebFetch sends content through the closure."""
        summarise_mock = AsyncMock(return_value="SUMMARISED OUTPUT")
        ctx = _ctx(tmp_path, summarise=summarise_mock)
        tool = WebFetchTool()

        with patch(
            "kernel.tools.web.fetch_backends.fetch_with_fallback",
            new=AsyncMock(return_value=(_FakeResult("https://x", "raw page body"), "httpx")),
        ):
            results = []
            async for ev in tool.call(
                {"url": "https://x", "prompt": "what is it about?"}, ctx
            ):
                results.append(ev)

        assert len(results) == 1
        text = results[0].llm_content[0].text
        assert "SUMMARISED OUTPUT" in text
        assert "raw page body" not in text  # replaced by summary
        assert results[0].data["post_processed"] is True
        summarise_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_summariser_falls_back_to_raw_content(
        self, tmp_path: Path
    ) -> None:
        """When ctx.summarise is None, fall back to raw content (no crash)."""
        ctx = _ctx(tmp_path, summarise=None)
        tool = WebFetchTool()

        with patch(
            "kernel.tools.web.fetch_backends.fetch_with_fallback",
            new=AsyncMock(return_value=(_FakeResult("https://x", "raw body"), "httpx")),
        ):
            results = []
            async for ev in tool.call(
                {"url": "https://x", "prompt": "summarise"}, ctx
            ):
                results.append(ev)

        text = results[0].llm_content[0].text
        assert "raw body" in text
        assert results[0].data["post_processed"] is False

    @pytest.mark.asyncio
    async def test_no_prompt_never_invokes_summariser(self, tmp_path: Path) -> None:
        """When the LLM doesn't supply a prompt, stay out of secondary-model path."""
        summarise_mock = AsyncMock(return_value="should not appear")
        ctx = _ctx(tmp_path, summarise=summarise_mock)
        tool = WebFetchTool()

        with patch(
            "kernel.tools.web.fetch_backends.fetch_with_fallback",
            new=AsyncMock(return_value=(_FakeResult("https://x", "raw body"), "httpx")),
        ):
            results = []
            async for ev in tool.call({"url": "https://x"}, ctx):
                results.append(ev)

        text = results[0].llm_content[0].text
        assert "raw body" in text
        assert "should not appear" not in text
        summarise_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_summarise_exception_falls_back_gracefully(
        self, tmp_path: Path
    ) -> None:
        """Closure failure → raw content is returned (not an error)."""
        summarise_mock = AsyncMock(side_effect=RuntimeError("LLM 500"))
        ctx = _ctx(tmp_path, summarise=summarise_mock)
        tool = WebFetchTool()

        with patch(
            "kernel.tools.web.fetch_backends.fetch_with_fallback",
            new=AsyncMock(return_value=(_FakeResult("https://x", "raw body"), "httpx")),
        ):
            results = []
            async for ev in tool.call(
                {"url": "https://x", "prompt": "summarise"}, ctx
            ):
                results.append(ev)

        text = results[0].llm_content[0].text
        assert "raw body" in text
        assert results[0].data["post_processed"] is False


class TestMakeSecondaryModelPrompt:
    def test_preapproved_host_uses_concise_guidelines(self) -> None:
        out = _make_secondary_model_prompt("body", "question?", is_preapproved=True)
        assert "Provide a concise response" in out
        assert "125-character" not in out  # strict-quote guidelines omitted

    def test_non_preapproved_host_adds_strict_guidelines(self) -> None:
        out = _make_secondary_model_prompt("body", "question?", is_preapproved=False)
        assert "125-character" in out
        assert "song lyrics" in out
        assert "Open Source Software is ok" in out
