"""Canary tests for the DeepCLI first-principles prompt section."""

from __future__ import annotations

from kernel.orchestrator import OrchestratorDeps
from kernel.orchestrator.prompt_builder import PromptBuilder
from kernel.prompts.manager import PromptManager

from tests.kernel.orchestrator.conftest import FakeLLMProvider


class _EmptySkills:
    def get_skill_listing(self) -> str:
        return ""


def test_first_principles_prompt_loads() -> None:
    pm = PromptManager()
    pm.load()

    text = pm.get("orchestrator/first_principles")
    assert text.startswith("# First principles")
    assert "mandatory for every response" in text
    assert "decision filter for all work" in text
    assert "reason from first principles" in text
    assert "root cause" in text


async def test_first_principles_prompt_is_in_static_prefix() -> None:
    pm = PromptManager()
    pm.load()
    deps = OrchestratorDeps(provider=FakeLLMProvider(), prompts=pm)
    builder = PromptBuilder(session_id="first-principles", deps=deps)

    sections = await builder.build()

    static_text = sections[0].text
    assert sections[0].cache is True
    assert "# System" in static_text
    assert "# First principles" in static_text
    assert "# Doing tasks" in static_text
    assert static_text.index("# System") < static_text.index("# First principles")
    assert static_text.index("# First principles") < static_text.index("# Doing tasks")


async def test_repl_mode_uses_repl_tool_guidance() -> None:
    pm = PromptManager()
    pm.load()
    deps = OrchestratorDeps(provider=FakeLLMProvider(), prompts=pm)
    builder = PromptBuilder(session_id="repl-static", deps=deps)

    sections = await builder.build(repl_mode=True)

    static_text = sections[0].text
    assert "REPL mode is active" in static_text
    assert "hidden from direct top-level use" in static_text
    assert "To read files use Read instead of cat" not in static_text


async def test_empty_skill_listing_overrides_stale_skill_context() -> None:
    pm = PromptManager()
    pm.load()
    deps = OrchestratorDeps(provider=FakeLLMProvider(), prompts=pm, skills=_EmptySkills())
    builder = PromptBuilder(session_id="empty-skills", deps=deps)

    sections = await builder.build()
    text = "\n\n".join(section.text for section in sections)

    assert "# Available skills" in text
    assert "No model-invocable skills are currently available" in text
    assert "Ignore earlier available-skill listings in this conversation" in text
    assert "unless it appears in this current Available skills section" in text
