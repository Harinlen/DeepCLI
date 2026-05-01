"""Tests for prompt caching — Phase 5.5.1.

Covers:
  - system_to_anthropic() cache_control injection
  - Cacheable section merging + breakpoint limits
  - Prompt caching disabled via config
  - UsageInfo cache token fields
  - ModelUsage cache token propagation
  - Prefix stability across rounds
"""

from __future__ import annotations

from daemon.engine.context import PromptSection, build_system_prompt, prompt_sections_to_text
from daemon.engine.stream import UsageInfo
from daemon.providers.anthropic_format import (
    _MAX_CACHE_BREAKPOINTS,
    system_to_anthropic,
)
from daemon.sessions.meta import ModelUsage


# ------------------------------------------------------------------
# system_to_anthropic — cache_control injection
# ------------------------------------------------------------------


class TestSystemToAnthropic:
    def test_cacheable_sections_get_marker(self) -> None:
        sections = [
            PromptSection(text="static rules", cacheable=True),
            PromptSection(text="dynamic env", cacheable=False),
        ]
        blocks = system_to_anthropic(sections)
        assert isinstance(blocks, list)
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in blocks[1]

    def test_non_cacheable_sections_no_marker(self) -> None:
        sections = [
            PromptSection(text="dynamic a"),
            PromptSection(text="dynamic b"),
        ]
        blocks = system_to_anthropic(sections)
        assert isinstance(blocks, list)
        for block in blocks:
            assert "cache_control" not in block

    def test_adjacent_cacheable_merged_breakpoint(self) -> None:
        """Adjacent cacheable sections: only the last in the run gets the marker."""
        sections = [
            PromptSection(text="static 1", cacheable=True),
            PromptSection(text="static 2", cacheable=True),
            PromptSection(text="dynamic"),
        ]
        blocks = system_to_anthropic(sections)
        assert "cache_control" not in blocks[0]
        assert blocks[1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in blocks[2]

    def test_max_breakpoints_respected(self) -> None:
        """No more than _MAX_CACHE_BREAKPOINTS markers are placed."""
        # Create more non-contiguous cacheable sections than the limit.
        sections = []
        for i in range(_MAX_CACHE_BREAKPOINTS + 3):
            sections.append(PromptSection(text=f"cacheable {i}", cacheable=True))
            sections.append(PromptSection(text=f"dynamic {i}"))

        blocks = system_to_anthropic(sections)
        cache_count = sum(1 for b in blocks if "cache_control" in b)
        assert cache_count <= _MAX_CACHE_BREAKPOINTS

    def test_prompt_caching_disabled_returns_string(self) -> None:
        sections = [
            PromptSection(text="a", cacheable=True),
            PromptSection(text="b"),
        ]
        result = system_to_anthropic(sections, prompt_caching=False)
        assert isinstance(result, str)
        assert result == "a\n\nb"

    def test_all_text_preserved(self) -> None:
        """All section text appears in blocks regardless of caching."""
        sections = [
            PromptSection(text="alpha", cacheable=True),
            PromptSection(text="beta"),
            PromptSection(text="gamma", cacheable=True),
        ]
        blocks = system_to_anthropic(sections)
        texts = [b["text"] for b in blocks]
        assert texts == ["alpha", "beta", "gamma"]


# ------------------------------------------------------------------
# Prefix stability
# ------------------------------------------------------------------


class TestPrefixStability:
    """Ensure system prompt prefix is stable across rounds."""

    def test_static_section_unchanged_across_calls(self, tmp_path: object) -> None:
        """The first (cacheable) section is identical between calls."""
        s1 = build_system_prompt(cwd=tmp_path)  # type: ignore[arg-type]
        s2 = build_system_prompt(cwd=tmp_path)  # type: ignore[arg-type]
        assert s1[0].text == s2[0].text
        assert s1[0].cacheable is True

    def test_section_order_deterministic(self, tmp_path: object) -> None:
        """Section order is identical between calls with same args."""
        kwargs = {"cwd": tmp_path, "model_name": "test", "git_status": "main"}
        s1 = build_system_prompt(**kwargs)  # type: ignore[arg-type]
        s2 = build_system_prompt(**kwargs)  # type: ignore[arg-type]
        assert len(s1) == len(s2)
        for a, b in zip(s1, s2):
            assert a.text == b.text
            assert a.cacheable == b.cacheable

    def test_prompt_sections_to_text_roundtrip(self) -> None:
        sections = [
            PromptSection(text="hello", cacheable=True),
            PromptSection(text="world"),
        ]
        assert prompt_sections_to_text(sections) == "hello\n\nworld"


# ------------------------------------------------------------------
# UsageInfo cache fields
# ------------------------------------------------------------------


class TestUsageInfoCacheFields:
    def test_default_zero(self) -> None:
        u = UsageInfo()
        assert u.cache_creation_tokens == 0
        assert u.cache_read_tokens == 0

    def test_set_values(self) -> None:
        u = UsageInfo(
            input_tokens=100,
            output_tokens=50,
            cache_creation_tokens=1000,
            cache_read_tokens=5000,
        )
        assert u.cache_creation_tokens == 1000
        assert u.cache_read_tokens == 5000


# ------------------------------------------------------------------
# ModelUsage cache fields
# ------------------------------------------------------------------


class TestModelUsageCacheFields:
    def test_default_zero(self) -> None:
        mu = ModelUsage()
        assert mu.cache_creation_tokens == 0
        assert mu.cache_read_tokens == 0

    def test_accumulation(self) -> None:
        mu = ModelUsage()
        mu.cache_creation_tokens += 500
        mu.cache_read_tokens += 3000
        assert mu.cache_creation_tokens == 500
        assert mu.cache_read_tokens == 3000

    def test_serialization_roundtrip(self) -> None:
        mu = ModelUsage(
            input_tokens=10,
            output_tokens=20,
            cache_creation_tokens=100,
            cache_read_tokens=200,
        )
        d = mu.model_dump()
        assert d["cache_creation_tokens"] == 100
        assert d["cache_read_tokens"] == 200
        mu2 = ModelUsage.model_validate(d)
        assert mu2 == mu

    def test_backward_compat_no_cache_fields(self) -> None:
        """Old .meta.json without cache fields still loads."""
        d = {"input_tokens": 10, "output_tokens": 20}
        mu = ModelUsage.model_validate(d)
        assert mu.cache_creation_tokens == 0
        assert mu.cache_read_tokens == 0
