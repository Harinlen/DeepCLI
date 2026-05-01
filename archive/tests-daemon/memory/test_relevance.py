"""Tests for the LLM-based memory relevance selector (Phase 5.7B).

Covers: filename parsing, selection flow, fallback on error,
already-surfaced filtering, and session reset.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from daemon.config.schema import MemoryRelevanceRuntimeConfig
from daemon.engine.stream import StreamEnd, StreamEvent, TextDelta, UsageInfo
from daemon.memory.relevance import MemorySelector, _parse_filename_list, _filename_of
from daemon.memory.schema import MemoryFrontmatter, MemoryRecord, MemoryType
from daemon.providers.base import Message, ModelInfo, Provider, ToolDefinition


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_record(
    type_: MemoryType,
    filename: str,
    description: str,
) -> MemoryRecord:
    """Create a test MemoryRecord."""
    from pathlib import Path

    relative = f"{type_.value}/{filename}"
    return MemoryRecord(
        relative=relative,
        path=Path(f"/fake/{relative}"),
        frontmatter=MemoryFrontmatter(
            name=filename.replace(".md", ""),
            description=description,
            type=type_,
        ),
        size_bytes=100,
    )


class FakeSelectorProvider(Provider):
    """Provider that returns a configurable JSON response."""

    name = "fake-selector"

    def __init__(self, response_text: str = '["user/role.md"]') -> None:
        self._response_text = response_text

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        yield TextDelta(content=self._response_text)
        yield StreamEnd(usage=UsageInfo(input_tokens=5, output_tokens=5))

    async def models(self) -> list[ModelInfo]:
        return []


class ErrorSelectorProvider(Provider):
    """Provider that raises an error during stream."""

    name = "error-selector"

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        raise RuntimeError("LLM unavailable")
        yield  # pragma: no cover

    async def models(self) -> list[ModelInfo]:
        return []


# ------------------------------------------------------------------
# Tests — _parse_filename_list
# ------------------------------------------------------------------


class TestParseFilenameList:
    """Tests for JSON filename list parsing."""

    def test_clean_json(self) -> None:
        assert _parse_filename_list('["a.md", "b.md"]') == ["a.md", "b.md"]

    def test_empty_list(self) -> None:
        assert _parse_filename_list("[]") == []

    def test_json_with_prose(self) -> None:
        text = 'Here are the relevant memories:\n["user/role.md", "feedback/test.md"]\n'
        assert _parse_filename_list(text) == ["user/role.md", "feedback/test.md"]

    def test_invalid_json(self) -> None:
        assert _parse_filename_list("not json at all") == []

    def test_non_string_entries_filtered(self) -> None:
        assert _parse_filename_list('["a.md", 42, "b.md"]') == ["a.md", "b.md"]


# ------------------------------------------------------------------
# Tests — _filename_of
# ------------------------------------------------------------------


class TestFilenameOf:
    """Tests for the filename extraction helper."""

    def test_relative_path(self) -> None:
        assert _filename_of("user/role.md") == "role.md"

    def test_bare_filename(self) -> None:
        assert _filename_of("role.md") == "role.md"


# ------------------------------------------------------------------
# Tests — MemorySelector
# ------------------------------------------------------------------


class TestMemorySelector:
    """Tests for the LLM-based selection flow."""

    @pytest.mark.asyncio
    async def test_select_returns_matching_records(self) -> None:
        """Selector returns records whose filenames match LLM response."""
        records = [
            _make_record(MemoryType.USER, "role.md", "user is a backend engineer"),
            _make_record(MemoryType.FEEDBACK, "testing.md", "prefers pytest"),
            _make_record(MemoryType.REFERENCE, "links.md", "grafana dashboard URL"),
        ]
        provider = FakeSelectorProvider(response_text='["role.md", "testing.md"]')
        cfg = MemoryRelevanceRuntimeConfig(top_k=5, timeout=10)
        selector = MemorySelector(provider, cfg)

        selected = await selector.select("how should I test this?", records)
        names = [r.frontmatter.name for r in selected]
        assert "role" in names
        assert "testing" in names
        assert "links" not in names

    @pytest.mark.asyncio
    async def test_select_handles_relative_paths(self) -> None:
        """LLM can return full relative paths or bare filenames."""
        records = [
            _make_record(MemoryType.USER, "role.md", "engineer"),
        ]
        provider = FakeSelectorProvider(response_text='["user/role.md"]')
        cfg = MemoryRelevanceRuntimeConfig(top_k=5, timeout=10)
        selector = MemorySelector(provider, cfg)

        selected = await selector.select("who am I?", records)
        assert len(selected) == 1

    @pytest.mark.asyncio
    async def test_select_empty_on_no_match(self) -> None:
        """Empty LLM response returns empty list."""
        records = [_make_record(MemoryType.USER, "role.md", "engineer")]
        provider = FakeSelectorProvider(response_text="[]")
        cfg = MemoryRelevanceRuntimeConfig(top_k=5, timeout=10)
        selector = MemorySelector(provider, cfg)

        selected = await selector.select("unrelated query", records)
        assert selected == []

    @pytest.mark.asyncio
    async def test_select_fallback_on_error(self) -> None:
        """Provider error returns empty list (caller falls back to full index)."""
        records = [_make_record(MemoryType.USER, "role.md", "engineer")]
        provider = ErrorSelectorProvider()
        cfg = MemoryRelevanceRuntimeConfig(top_k=5, timeout=10)
        selector = MemorySelector(provider, cfg)

        selected = await selector.select("hello", records)
        assert selected == []

    @pytest.mark.asyncio
    async def test_already_surfaced_filtering(self) -> None:
        """Records already surfaced in this session are excluded."""
        records = [
            _make_record(MemoryType.USER, "role.md", "engineer"),
            _make_record(MemoryType.FEEDBACK, "test.md", "prefers pytest"),
        ]
        # First call surfaces role.md.
        provider = FakeSelectorProvider(response_text='["role.md"]')
        cfg = MemoryRelevanceRuntimeConfig(top_k=5, timeout=10)
        selector = MemorySelector(provider, cfg)

        await selector.select("q1", records)

        # Second call — role.md should not appear in manifest.
        # The provider always returns role.md, but it's filtered from candidates.
        provider2 = FakeSelectorProvider(response_text='["role.md"]')
        selector._provider = provider2
        selected = await selector.select("q2", records)
        # role.md was already surfaced, so not in candidates → not selected.
        assert all(r.frontmatter.name != "role" for r in selected)

    @pytest.mark.asyncio
    async def test_reset_session(self) -> None:
        """reset_session clears the surfaced set."""
        records = [_make_record(MemoryType.USER, "role.md", "engineer")]
        provider = FakeSelectorProvider(response_text='["role.md"]')
        cfg = MemoryRelevanceRuntimeConfig(top_k=5, timeout=10)
        selector = MemorySelector(provider, cfg)

        await selector.select("q1", records)
        assert len(selector._already_surfaced) > 0

        selector.reset_session()
        assert len(selector._already_surfaced) == 0
