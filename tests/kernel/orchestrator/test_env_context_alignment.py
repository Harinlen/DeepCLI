"""Canary tests for the environment-context section.

Mirrors CC's ``computeSimpleEnvInfo()`` (prompts.ts:651-710), with three
deliberate deviations documented in ``_build_env_context``:

1. No marketing-name lookup — we use CC's null-marketing-name fallback
   phrasing (``prompts.ts:627``) for every model.
2. No knowledge-cutoff line — only meaningful for Claude models and the
   WebSearch tool covers "is this post-cutoff?" cases.
3. No CC product-marketing lines (``most recent Claude model family`` /
   ``Claude Code is available`` / ``Fast mode``).

These tests pin those deviations so a well-intentioned future PR can't
silently copy CC's text back in — or forget to emit the model line at
all once ``build(model=...)`` is wired.
"""

from __future__ import annotations

from pathlib import Path

from kernel.llm.config import ModelRef
from kernel.orchestrator.prompt_builder import PromptBuilder


# ---------------------------------------------------------------------------
# Line presence / absence (direct calls to the staticmethod)
# ---------------------------------------------------------------------------


class TestEnvContextShape:
    def test_header_and_required_fields(self, tmp_path: Path) -> None:
        """Header + the six environment bullets are always present."""
        text = PromptBuilder._build_env_context(tmp_path)
        assert text.startswith("# Environment\n")
        assert "You have been invoked in the following environment:" in text
        assert f" - Primary working directory: {tmp_path}" in text
        assert "  - Is a git repository: False" in text  # tmp_path has no .git
        assert " - Platform: " in text
        assert " - Shell: " in text
        assert " - OS Version: " in text
        assert " - Date/time (UTC): " in text

    def test_model_line_absent_when_model_none(self, tmp_path: Path) -> None:
        """Without ``model``, the model line is not emitted."""
        text = PromptBuilder._build_env_context(tmp_path, model=None)
        assert "You are powered by the model" not in text

    def test_model_line_present_when_model_given(self, tmp_path: Path) -> None:
        """With a ``ModelRef``, the CC-fallback phrasing is appended."""
        ref = ModelRef(provider="anthropic", model="claude-opus-4-7")
        text = PromptBuilder._build_env_context(tmp_path, model=ref)
        assert " - You are powered by the model claude-opus-4-7." in text

    def test_model_line_provider_agnostic(self, tmp_path: Path) -> None:
        """Non-Claude IDs (OpenAI, Qwen, etc.) get the same phrasing."""
        for ref in (
            ModelRef(provider="openai", model="gpt-4o"),
            ModelRef(provider="local-qwen", model="qwen2.5-coder-32b"),
            ModelRef(provider="bedrock", model="us.anthropic.claude-opus-4-6"),
        ):
            text = PromptBuilder._build_env_context(tmp_path, model=ref)
            assert f" - You are powered by the model {ref.model}." in text

    def test_git_detection(self, tmp_path: Path) -> None:
        """Existence of ``.git`` flips the ``Is a git repository`` flag."""
        (tmp_path / ".git").mkdir()
        text = PromptBuilder._build_env_context(tmp_path)
        assert "  - Is a git repository: True" in text


# ---------------------------------------------------------------------------
# Conflict guard — CC-only text must NOT leak into Mustang's env context
# ---------------------------------------------------------------------------


class TestOmittedCCText:
    """Each assertion guards a deliberate deviation from CC's
    ``computeSimpleEnvInfo``.  If a regression copies CC's full bullet
    list back in unconditionally, one of these fails loud."""

    def test_no_knowledge_cutoff_line(self, tmp_path: Path) -> None:
        ref = ModelRef(provider="anthropic", model="claude-opus-4-6")
        text = PromptBuilder._build_env_context(tmp_path, model=ref)
        assert "knowledge cutoff" not in text.lower()

    def test_no_marketing_name_framing(self, tmp_path: Path) -> None:
        """``You are powered by the model named X. The exact model ID is Y.``
        is CC's when-we-know-the-marketing-name branch.  Mustang always
        takes the simpler fallback."""
        ref = ModelRef(provider="anthropic", model="claude-opus-4-6")
        text = PromptBuilder._build_env_context(tmp_path, model=ref)
        assert "named " not in text
        assert "The exact model ID is" not in text
        assert "(with 1M context)" not in text

    def test_no_product_marketing_bullets(self, tmp_path: Path) -> None:
        """CC's three trailing product bullets must stay out."""
        ref = ModelRef(provider="anthropic", model="claude-opus-4-6")
        text = PromptBuilder._build_env_context(tmp_path, model=ref)
        assert "Claude Code is available" not in text
        assert "Fast mode" not in text
        assert "most recent Claude model family" not in text
