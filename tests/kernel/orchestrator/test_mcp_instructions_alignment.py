"""Canary tests for the ``# MCP Server Instructions`` section (Phase 4).

Mirrors CC's ``getMcpInstructions()`` (prompts.ts:579-604), placed between
``language`` and git context in Mustang's system-prompt ordering (CC places
it after ``output_style``, which Mustang does not implement).

These tests pin:

1. The exact CC phrasing for header + intro — a regression that paraphrases
   either sentence breaks loud.
2. ``{blocks}`` placeholder is present in the raw template and filled on render.
3. Absence of the section under three degraded conditions: no closure,
   empty list, all servers have empty instructions.
4. Single-server and multi-server block formatting.
5. ``cache=False`` — MCP servers can connect/disconnect between turns
   (CC rationale: DANGEROUS_uncachedSystemPromptSection, prompts.ts:513-520).
6. Placement immediately after language section (or env context when language
   is absent).
7. Reverse guards: CC feature-flag text must not leak into Mustang.
"""

from __future__ import annotations

import pytest

from kernel.llm.config import ModelRef
from kernel.llm.types import PromptSection
from kernel.orchestrator.prompt_builder import PromptBuilder
from kernel.prompts.manager import PromptManager

_CC_HEADER = "# MCP Server Instructions"
_CC_INTRO = (
    "The following MCP servers have provided instructions for how to use their tools and resources:"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prompts() -> PromptManager:
    pm = PromptManager()
    pm.load()
    return pm


class _MinimalDeps:
    """Stand-in for OrchestratorDeps covering just what PromptBuilder.build()
    reads.  Keeps direct-build tests independent of the wider dataclass."""

    def __init__(
        self,
        *,
        prompts: PromptManager | None,
        mcp_instructions: object = None,
    ) -> None:
        self.prompts = prompts
        self.memory = None
        self.skills = None
        self.git = None
        self.mcp_instructions = mcp_instructions


async def _build_sections(
    pm: PromptManager,
    *,
    mcp_instructions: object = None,
    language: str | None = None,
) -> list[PromptSection]:
    deps = _MinimalDeps(prompts=pm, mcp_instructions=mcp_instructions)
    builder = PromptBuilder(session_id="test-mcp-alignment", deps=deps)
    return await builder.build(
        "probe",
        model=ModelRef(provider="fake", model="fake-model"),
        language=language,
    )


# ---------------------------------------------------------------------------
# Template-level canaries — the .txt file itself
# ---------------------------------------------------------------------------


class TestTemplate:
    def test_template_loaded(self, prompts: PromptManager) -> None:
        assert prompts.has("orchestrator/mcp_instructions")

    def test_header_exact(self, prompts: PromptManager) -> None:
        """Raw template starts with the CC header line."""
        raw = prompts.get("orchestrator/mcp_instructions")
        assert raw.startswith(_CC_HEADER)

    def test_intro_exact(self, prompts: PromptManager) -> None:
        """CC intro sentence is present verbatim (prompts.ts:601)."""
        raw = prompts.get("orchestrator/mcp_instructions")
        assert _CC_INTRO in raw

    def test_blocks_placeholder_present(self, prompts: PromptManager) -> None:
        """``{blocks}`` placeholder exists so render() can fill it."""
        raw = prompts.get("orchestrator/mcp_instructions")
        assert "{blocks}" in raw

    def test_render_fills_placeholder(self, prompts: PromptManager) -> None:
        rendered = prompts.render("orchestrator/mcp_instructions", blocks="## srv\ndo stuff")
        assert "{blocks}" not in rendered
        assert "## srv\ndo stuff" in rendered

    def test_render_preserves_header_and_intro(self, prompts: PromptManager) -> None:
        rendered = prompts.render("orchestrator/mcp_instructions", blocks="## x\ny")
        assert rendered.startswith(_CC_HEADER)
        assert _CC_INTRO in rendered


# ---------------------------------------------------------------------------
# Injection conditions
# ---------------------------------------------------------------------------


class TestSectionInjection:
    async def test_absent_when_getter_is_none(self, prompts: PromptManager) -> None:
        sections = await _build_sections(prompts, mcp_instructions=None)
        for s in sections:
            assert _CC_HEADER not in s.text

    async def test_absent_when_getter_returns_empty(self, prompts: PromptManager) -> None:
        sections = await _build_sections(prompts, mcp_instructions=lambda: [])
        for s in sections:
            assert _CC_HEADER not in s.text

    async def test_absent_when_all_instructions_empty(self, prompts: PromptManager) -> None:
        sections = await _build_sections(
            prompts,
            mcp_instructions=lambda: [("srv", ""), ("srv2", None)],
        )
        for s in sections:
            assert _CC_HEADER not in s.text

    async def test_single_server(self, prompts: PromptManager) -> None:
        sections = await _build_sections(
            prompts,
            mcp_instructions=lambda: [("my-server", "use tool foo for bar")],
        )
        mcp_sections = [s for s in sections if s.text.startswith(_CC_HEADER)]
        assert len(mcp_sections) == 1
        text = mcp_sections[0].text
        assert "## my-server\nuse tool foo for bar" in text

    async def test_multi_server_separated_by_blank_line(self, prompts: PromptManager) -> None:
        sections = await _build_sections(
            prompts,
            mcp_instructions=lambda: [
                ("server-a", "instruction A"),
                ("server-b", "instruction B"),
            ],
        )
        mcp_sections = [s for s in sections if s.text.startswith(_CC_HEADER)]
        assert len(mcp_sections) == 1
        text = mcp_sections[0].text
        assert "## server-a\ninstruction A" in text
        assert "## server-b\ninstruction B" in text
        # blocks are joined with double newline
        assert "instruction A\n\n## server-b" in text

    async def test_servers_with_empty_instructions_filtered_out(
        self, prompts: PromptManager
    ) -> None:
        """Servers with falsy instructions must be silently skipped."""
        sections = await _build_sections(
            prompts,
            mcp_instructions=lambda: [
                ("has-instructions", "do X"),
                ("no-instructions", ""),
            ],
        )
        mcp_sections = [s for s in sections if s.text.startswith(_CC_HEADER)]
        assert len(mcp_sections) == 1
        assert "## has-instructions" in mcp_sections[0].text
        assert "no-instructions" not in mcp_sections[0].text

    async def test_cache_false(self, prompts: PromptManager) -> None:
        """MCP servers can connect/disconnect between turns — must not cache."""
        sections = await _build_sections(
            prompts,
            mcp_instructions=lambda: [("srv", "instr")],
        )
        mcp_sections = [s for s in sections if s.text.startswith(_CC_HEADER)]
        assert mcp_sections[0].cache is False


# ---------------------------------------------------------------------------
# Ordering canaries
# ---------------------------------------------------------------------------


class TestPlacement:
    async def test_after_language_and_before_env_when_language_set(
        self, prompts: PromptManager
    ) -> None:
        """Mustang ordering: language (cacheable) → mcp_instructions
        (volatile) → env (last, with timestamp)."""
        sections = await _build_sections(
            prompts,
            mcp_instructions=lambda: [("srv", "instr")],
            language="English",
        )
        lang_idx: int | None = None
        mcp_idx: int | None = None
        env_idx: int | None = None
        for i, s in enumerate(sections):
            if s.text.startswith("# Language\n"):
                lang_idx = i
            elif s.text.startswith(_CC_HEADER):
                mcp_idx = i
            elif s.text.startswith("# Environment\n"):
                env_idx = i
        assert lang_idx is not None, "language section missing"
        assert mcp_idx is not None, "mcp section missing"
        assert env_idx is not None, "env section missing"
        assert lang_idx < mcp_idx < env_idx, (
            f"expected language < mcp < env; "
            f"got lang_idx={lang_idx}, mcp_idx={mcp_idx}, env_idx={env_idx}"
        )

    async def test_before_env_when_no_language(self, prompts: PromptManager) -> None:
        """Without a language section, mcp_instructions still belongs in
        the volatile block before the trailing env context."""
        sections = await _build_sections(
            prompts,
            mcp_instructions=lambda: [("srv", "instr")],
            language=None,
        )
        env_idx: int | None = None
        mcp_idx: int | None = None
        for i, s in enumerate(sections):
            if s.text.startswith("# Environment\n"):
                env_idx = i
            elif s.text.startswith(_CC_HEADER):
                mcp_idx = i
        assert env_idx is not None, "env section missing"
        assert mcp_idx is not None, "mcp section missing"
        assert mcp_idx < env_idx, (
            f"mcp must precede env (env is last); got env_idx={env_idx}, mcp_idx={mcp_idx}"
        )


# ---------------------------------------------------------------------------
# Reverse guards — CC feature-flag text must not leak
# ---------------------------------------------------------------------------


class TestOmittedCCText:
    async def test_no_delta_feature_flag_text(self, prompts: PromptManager) -> None:
        sections = await _build_sections(
            prompts,
            mcp_instructions=lambda: [("srv", "instr")],
        )
        full = "\n".join(s.text for s in sections)
        assert "isMcpInstructionsDeltaEnabled" not in full
        assert "mcp_instructions_delta" not in full
        assert "attachments.ts" not in full
