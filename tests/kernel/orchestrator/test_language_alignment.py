"""Canary tests for the ``# Language`` section (Phase 5).

Mirrors CC's ``getLanguageSection()`` (prompts.ts:142-149), placed between
``env_info_simple`` and ``mcp_instructions`` in CC's system-prompt
ordering (prompts.ts:499-504).

These tests pin:

1. The exact CC phrasing — a regression that paraphrases the two
   sentences breaks loud.
2. Absence of the section when ``language=None`` — CC's nullable
   behaviour (no section at all, LLM picks language from context).
3. Placement between env context and everything that follows — matches
   CC's ordering precisely.
4. ``cache=True`` — stable across turns while the user's preference is
   unchanged, unlike env context which carries a timestamp.
5. Multilingual parity — the same rendering path works for CJK and
   Latin language names (no single-language optimisation per memory).

Tests use ``pytest-asyncio`` auto mode (see ``pyproject.toml``), so
``async def test_*`` runs as a coroutine without decorators.
"""

from __future__ import annotations

import pytest

from kernel.llm.config import ModelRef
from kernel.llm.types import PromptSection
from kernel.orchestrator import OrchestratorConfig, OrchestratorDeps
from kernel.orchestrator.orchestrator import StandardOrchestrator
from kernel.orchestrator.prompt_builder import PromptBuilder
from kernel.prompts.manager import PromptManager

# Re-use the FakeLLMProvider fixture from conftest.
from tests.kernel.orchestrator.conftest import FakeLLMProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prompts() -> PromptManager:
    pm = PromptManager()
    pm.load()
    return pm


class _MinimalDeps:
    """Stand-in for ``OrchestratorDeps`` covering just the attributes
    PromptBuilder.build() reads.  Keeps direct-build tests independent
    of the wider dataclass — a new optional dep won't force a test edit."""

    def __init__(self, *, prompts: PromptManager | None) -> None:
        self.prompts = prompts
        self.memory = None
        self.skills = None
        self.git = None


def _make_orchestrator(pm: PromptManager, *, language: str | None) -> StandardOrchestrator:
    """Build a StandardOrchestrator wired with ``pm`` and ``language``.

    Exercises the ``OrchestratorConfig.language`` field and the
    ``build(language=...)`` signature — the two seams Phase 5 adds.
    """
    deps = OrchestratorDeps(provider=FakeLLMProvider(), prompts=pm)
    return StandardOrchestrator(
        deps=deps,
        session_id="test-language-alignment",
        config=OrchestratorConfig(
            model=ModelRef(provider="fake", model="fake-model"),
            temperature=None,
            language=language,
        ),
    )


async def _build_sections(pm: PromptManager, *, language: str | None) -> list[PromptSection]:
    orch = _make_orchestrator(pm, language=language)
    return await orch._prompt_builder.build(
        "probe",
        model=orch._config.model,
        language=orch._config.language,
    )


# ---------------------------------------------------------------------------
# Template-level canaries — the .txt file itself
# ---------------------------------------------------------------------------


class TestTemplate:
    def test_template_loaded(self, prompts: PromptManager) -> None:
        assert prompts.has("orchestrator/language")

    def test_exact_cc_phrasing(self, prompts: PromptManager) -> None:
        """Render with a known language and compare byte-for-byte against
        the expected CC output for the same language."""
        rendered = prompts.render("orchestrator/language", language="English")
        expected = (
            "# Language\n"
            "Always respond in English. Use English for all explanations, "
            "comments, and communications with the user. Technical terms "
            "and code identifiers should remain in their original form."
        )
        assert rendered == expected

    def test_placeholder_applied_both_occurrences(self, prompts: PromptManager) -> None:
        """CC's template uses ``${languagePreference}`` twice — Mustang's
        ``{language}`` must likewise appear twice so both slots get
        filled.  A regression that drops one placeholder changes the
        sentence but could still pass a single-substring check."""
        rendered = prompts.render("orchestrator/language", language="中文")
        assert rendered.count("中文") == 2


# ---------------------------------------------------------------------------
# Integration canaries — build() behaviour through the real orchestrator
# ---------------------------------------------------------------------------


class TestSectionInjection:
    async def test_absent_when_language_none(self, prompts: PromptManager) -> None:
        sections = await _build_sections(prompts, language=None)
        for s in sections:
            assert "# Language" not in s.text

    async def test_present_when_language_set(self, prompts: PromptManager) -> None:
        sections = await _build_sections(prompts, language="English")
        language_sections = [s for s in sections if s.text.startswith("# Language\n")]
        assert len(language_sections) == 1
        assert "Always respond in English." in language_sections[0].text

    async def test_multilingual_chinese(self, prompts: PromptManager) -> None:
        """CJK language names must flow through str.format cleanly."""
        sections = await _build_sections(prompts, language="中文")
        language_sections = [s for s in sections if s.text.startswith("# Language\n")]
        assert len(language_sections) == 1
        assert "Always respond in 中文." in language_sections[0].text
        assert "Use 中文 for all explanations" in language_sections[0].text

    async def test_cache_true(self, prompts: PromptManager) -> None:
        """Stable across turns while preference is unchanged — unlike
        env context above which carries a timestamp."""
        sections = await _build_sections(prompts, language="Français")
        language_sections = [s for s in sections if s.text.startswith("# Language\n")]
        assert language_sections[0].cache is True

    async def test_placement_before_env_context(self, prompts: PromptManager) -> None:
        """Mustang places ``# Environment`` last (it carries a live
        timestamp).  ``# Language`` is cacheable and must therefore live
        in the stable prefix, strictly before env context."""
        sections = await _build_sections(prompts, language="English")
        env_idx: int | None = None
        lang_idx: int | None = None
        for i, s in enumerate(sections):
            if s.text.startswith("# Environment\n"):
                env_idx = i
            elif s.text.startswith("# Language\n"):
                lang_idx = i
        assert env_idx is not None, "env context section missing"
        assert lang_idx is not None, "language section missing"
        assert lang_idx < env_idx, (
            f"language must precede env context (env is last); "
            f"got env_idx={env_idx}, lang_idx={lang_idx}"
        )


# ---------------------------------------------------------------------------
# set_config patch canaries — the new language field survives patching
# ---------------------------------------------------------------------------


class TestConfigPatch:
    def test_patch_language_updates_config(self, prompts: PromptManager) -> None:
        from kernel.orchestrator import OrchestratorConfigPatch

        orch = _make_orchestrator(prompts, language=None)
        assert orch._config.language is None

        orch.set_config(OrchestratorConfigPatch(language="English"))
        assert orch._config.language == "English"

    def test_patch_language_none_preserves(self, prompts: PromptManager) -> None:
        """``patch.language=None`` means "leave unchanged" — consistent
        with how ``model`` / ``temperature`` / ``streaming_tools`` already
        behave in ``set_config``."""
        from kernel.orchestrator import OrchestratorConfigPatch

        orch = _make_orchestrator(prompts, language="English")
        orch.set_config(OrchestratorConfigPatch(language=None))
        assert orch._config.language == "English"


# ---------------------------------------------------------------------------
# Unit — PromptBuilder.build() called directly without the orchestrator.
# Lets a future refactor trust that the method contract itself is stable.
# ---------------------------------------------------------------------------


class TestPromptBuilderDirect:
    async def test_direct_build_renders_section(self, prompts: PromptManager) -> None:
        deps = _MinimalDeps(prompts=prompts)
        pb = PromptBuilder(session_id="direct", deps=deps)

        sections = await pb.build(language="English")
        language_sections = [s for s in sections if s.text.startswith("# Language\n")]
        assert len(language_sections) == 1
        assert language_sections[0].cache is True

    async def test_direct_build_skips_when_prompts_missing(self) -> None:
        """No PromptManager → no language section, even when language set."""

        pb = PromptBuilder(session_id="direct", deps=_MinimalDeps(prompts=None))
        sections = await pb.build(language="English")
        for s in sections:
            assert "# Language" not in s.text


# ---------------------------------------------------------------------------
# Conflict guard — make sure nothing CC-adjacent leaked in
# ---------------------------------------------------------------------------


class TestNoLeakage:
    """CC's ``getLanguageSection()`` is very short; this guard exists to
    catch regressions that accidentally inline CC's settings-loading
    machinery or its source variable name."""

    def test_no_settings_machinery(self, prompts: PromptManager) -> None:
        text = prompts.get("orchestrator/language")
        assert "settings" not in text.lower()
        assert "getInitialSettings" not in text
        assert "languagePreference" not in text
