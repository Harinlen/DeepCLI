"""Canary tests for the session-specific guidance section.

Mirrors CC's ``getSessionSpecificGuidanceSection()`` (prompts.ts:352-400).
Each bullet lives in its own file under
``prompts/default/orchestrator/session_guidance/`` and is conditionally
included based on the tool snapshot available for the turn.

These tests do not byte-compare against CC — they assert the key
phrases that signal the behavioural cue we care about is still present.
If a future refactor drops the file or mangles the text, one of these
assertions breaks loud.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from kernel.llm.config import ModelRef
from kernel.orchestrator import OrchestratorConfig, OrchestratorDeps
from kernel.orchestrator.orchestrator import StandardOrchestrator
from kernel.prompts.manager import PromptManager

# Re-use the FakeLLMProvider fixture from conftest.
from tests.kernel.orchestrator.conftest import FakeLLMProvider  # noqa: F401


@pytest.fixture(scope="module")
def prompts() -> PromptManager:
    pm = PromptManager()
    pm.load()
    return pm


@pytest.fixture
def make_orch(prompts: PromptManager) -> Callable[..., StandardOrchestrator]:
    """Build a StandardOrchestrator with prompts wired into deps."""

    def _make(skills: Any = None) -> StandardOrchestrator:
        deps = OrchestratorDeps(
            provider=FakeLLMProvider(),
            prompts=prompts,
            skills=skills,
        )
        return StandardOrchestrator(
            deps=deps,
            session_id="test-session-guidance",
            config=OrchestratorConfig(
                model=ModelRef(provider="fake", model="fake-model"),
                temperature=None,
            ),
        )

    return _make


# ---------------------------------------------------------------------------
# Bullet-level canaries
# ---------------------------------------------------------------------------


class TestBulletContents:
    def test_deny_ask_loaded(self, prompts: PromptManager) -> None:
        text = prompts.get("orchestrator/session_guidance/deny_ask")
        assert "denied a tool call" in text
        assert "AskUserQuestion" in text

    def test_interactive_shell_loaded(self, prompts: PromptManager) -> None:
        text = prompts.get("orchestrator/session_guidance/interactive_shell")
        assert "`! <command>`" in text
        assert "gcloud auth login" in text

    def test_agent_tool_loaded(self, prompts: PromptManager) -> None:
        text = prompts.get("orchestrator/session_guidance/agent_tool")
        assert "Agent tool" in text
        assert "Subagents" in text
        assert "parallelizing independent queries" in text

    def test_search_direct_loaded(self, prompts: PromptManager) -> None:
        text = prompts.get("orchestrator/session_guidance/search_direct")
        assert "Glob or Grep" in text
        assert "directed codebase searches" in text

    def test_search_explore_agent_loaded(self, prompts: PromptManager) -> None:
        text = prompts.get("orchestrator/session_guidance/search_explore_agent")
        assert "subagent_type=Explore" in text
        assert "more than 3 queries" in text

    def test_skill_invoke_loaded(self, prompts: PromptManager) -> None:
        text = prompts.get("orchestrator/session_guidance/skill_invoke")
        assert "/<skill-name>" in text
        assert "user-invocable skill" in text
        assert "Skill tool" in text

    def test_bullets_have_no_leading_dash(self, prompts: PromptManager) -> None:
        """Builder adds ` - ` prefix — files must NOT include it themselves."""
        for key in (
            "deny_ask",
            "interactive_shell",
            "agent_tool",
            "search_direct",
            "search_explore_agent",
            "skill_invoke",
        ):
            text = prompts.get(f"orchestrator/session_guidance/{key}")
            assert not text.startswith("-"), f"{key}: leading dash leaks into builder"
            assert not text.startswith(" -"), f"{key}: leading dash leaks into builder"


# ---------------------------------------------------------------------------
# Conditional assembly
# ---------------------------------------------------------------------------


class _StubSkills:
    """Minimal SkillManager stand-in that reports at least one skill."""

    def get_skill_listing(self) -> str:
        return "# Available skills\n- /commit"


class TestConditionalAssembly:
    def test_empty_when_no_tools_and_no_skills(
        self, make_orch: Callable[..., StandardOrchestrator]
    ) -> None:
        """Non-interactive-style minimal shell: the only bullet present
        is the always-on interactive_shell hint.  Still non-None."""
        orch = make_orch()
        text = orch._build_session_guidance(enabled_tools=set(), has_skills=False)
        assert text is not None
        assert "# Session-specific guidance" in text
        assert "`! <command>`" in text

    def test_ask_user_question_gated(
        self, make_orch: Callable[..., StandardOrchestrator]
    ) -> None:
        orch = make_orch()
        without = orch._build_session_guidance(enabled_tools=set(), has_skills=False)
        with_ = orch._build_session_guidance(
            enabled_tools={"AskUserQuestion"}, has_skills=False
        )
        assert without is not None and with_ is not None
        assert "denied a tool call" not in without
        assert "denied a tool call" in with_

    def test_agent_bundle_appears_together(
        self, make_orch: Callable[..., StandardOrchestrator]
    ) -> None:
        """Agent bullet brings Glob/Grep and Explore bullets with it."""
        orch = make_orch()
        text = orch._build_session_guidance(
            enabled_tools={"Agent"}, has_skills=False
        )
        assert text is not None
        assert "Agent tool" in text
        assert "Glob or Grep" in text
        assert "subagent_type=Explore" in text

    def test_skill_requires_both_flag_and_tool(
        self, make_orch: Callable[..., StandardOrchestrator]
    ) -> None:
        orch = make_orch()

        no_skill_no_tool = orch._build_session_guidance(
            enabled_tools=set(), has_skills=False
        )
        only_flag = orch._build_session_guidance(
            enabled_tools=set(), has_skills=True
        )
        only_tool = orch._build_session_guidance(
            enabled_tools={"Skill"}, has_skills=False
        )
        both = orch._build_session_guidance(
            enabled_tools={"Skill"}, has_skills=True
        )

        assert no_skill_no_tool is not None
        assert only_flag is not None
        assert only_tool is not None
        assert both is not None

        assert "/<skill-name>" not in no_skill_no_tool
        assert "/<skill-name>" not in only_flag
        assert "/<skill-name>" not in only_tool
        assert "/<skill-name>" in both

    def test_full_bundle(
        self, make_orch: Callable[..., StandardOrchestrator]
    ) -> None:
        """Everything enabled → header + 6 bullets."""
        orch = make_orch()
        text = orch._build_session_guidance(
            enabled_tools={"AskUserQuestion", "Agent", "Skill"}, has_skills=True
        )
        assert text is not None
        assert text.startswith("# Session-specific guidance")
        lines = text.splitlines()
        bullet_lines = [ln for ln in lines if ln.startswith(" - ")]
        assert len(bullet_lines) == 6, (
            f"expected 6 bullets, got {len(bullet_lines)}:\n{text}"
        )

    def test_agent_bullets_present_in_plan_mode_tool_set(
        self, make_orch: Callable[..., StandardOrchestrator]
    ) -> None:
        """CC parity: session guidance is unchanged in plan mode.

        AgentTool (kind=orchestrate) survives plan-mode snapshot filtering,
        so ``snapshot_tool_names`` still contains "Agent", and all three
        agent-related guidance bullets must appear.
        """
        orch = make_orch()
        # Mirrors {s.name for s in snapshot.schemas} when plan_mode=True:
        # mutating tools (Bash, FileEdit, FileWrite) are absent from schemas;
        # Agent (orchestrate) and ExitPlanMode (other) survive into schemas.
        plan_mode_tool_names = {
            "Agent",
            "AskUserQuestion",
            "ExitPlanMode",
            "FileRead",
            "Glob",
            "Grep",
            "TodoWrite",
            "ToolSearch",
        }
        text = orch._build_session_guidance(
            enabled_tools=plan_mode_tool_names, has_skills=False
        )
        assert text is not None
        assert "# Session-specific guidance" in text
        assert "Agent tool" in text, "agent_tool bullet missing in plan mode"
        assert "Glob or Grep" in text, "search_direct bullet missing in plan mode"
        assert "subagent_type=Explore" in text, "search_explore bullet missing in plan mode"

    def test_inject_session_guidance_appends_section(
        self, make_orch: Callable[..., StandardOrchestrator]
    ) -> None:
        """``_inject_session_guidance`` appends to the system prompt list."""
        orch = make_orch(skills=_StubSkills())
        from kernel.llm.types import PromptSection

        sections: list[PromptSection] = []
        orch._inject_session_guidance(sections, {"AskUserQuestion", "Agent", "Skill"})
        assert len(sections) == 1
        section = sections[0]
        assert section.cache is False
        assert "# Session-specific guidance" in section.text
        assert "/<skill-name>" in section.text  # skills stub is non-empty

    def test_inject_noop_when_prompts_absent(
        self,
    ) -> None:
        """If deps.prompts is None, _build_ returns None and _inject_
        does not append anything."""
        from kernel.llm.types import PromptSection
        from tests.kernel.orchestrator.conftest import FakeLLMProvider

        deps = OrchestratorDeps(provider=FakeLLMProvider(), prompts=None)
        orch = StandardOrchestrator(
            deps=deps,
            session_id="test",
            config=OrchestratorConfig(
                model=ModelRef(provider="fake", model="fake-model"),
                temperature=None,
            ),
        )

        sections: list[PromptSection] = []
        orch._inject_session_guidance(sections, {"Agent", "Skill"})
        assert sections == []


# ---------------------------------------------------------------------------
# Guards against smuggling CC's unavailable bullets into Mustang
# ---------------------------------------------------------------------------


class TestOmittedCCBranches:
    """These CC branches should NOT leak into Mustang — they gate on
    features Mustang doesn't have.  Catch any regression that copies
    them in unconditionally."""

    def test_no_fork_subagent_text(
        self, make_orch: Callable[..., StandardOrchestrator]
    ) -> None:
        orch = make_orch()
        text = orch._build_session_guidance(
            enabled_tools={"AskUserQuestion", "Agent", "Skill"}, has_skills=True
        )
        assert text is not None
        assert "fork" not in text.lower()
        assert "runs in the background" not in text

    def test_no_verification_agent_text(
        self, make_orch: Callable[..., StandardOrchestrator]
    ) -> None:
        orch = make_orch()
        text = orch._build_session_guidance(
            enabled_tools={"AskUserQuestion", "Agent", "Skill"}, has_skills=True
        )
        assert text is not None
        assert "adversarial verification" not in text
        assert "verifier" not in text

    def test_no_discover_skills_text(
        self, make_orch: Callable[..., StandardOrchestrator]
    ) -> None:
        orch = make_orch()
        text = orch._build_session_guidance(
            enabled_tools={"AskUserQuestion", "Agent", "Skill"}, has_skills=True
        )
        assert text is not None
        assert "DiscoverSkills" not in text
        assert "Skills relevant to your task" not in text
