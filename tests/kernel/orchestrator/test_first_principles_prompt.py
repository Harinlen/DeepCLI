"""Canary tests for the DeepCLI first-principles prompt section."""

from __future__ import annotations

from kernel.orchestrator import OrchestratorDeps
from kernel.orchestrator.prompt_builder import PromptBuilder
from kernel.prompts.manager import PromptManager

from tests.kernel.orchestrator.conftest import FakeLLMProvider


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
