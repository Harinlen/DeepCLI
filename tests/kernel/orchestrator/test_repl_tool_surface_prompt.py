"""REPL prompt surface canaries."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from kernel.llm.config import ModelRef
from kernel.llm.types import PromptSection, ToolSchema
from kernel.orchestrator.loop.steps import build_prompt
from kernel.tools.registry import ToolSnapshot


class _PromptBuilder:
    def __init__(self) -> None:
        self.repl_mode: bool | None = None

    async def build(self, *_args: object, **kwargs: object) -> list[PromptSection]:
        self.repl_mode = bool(kwargs.get("repl_mode"))
        return [PromptSection(text="# Static prompt\nUse Read and Bash.", cache=True)]


class _ToolSource:
    def snapshot_for_session(self, **_kwargs: object) -> ToolSnapshot:
        repl = ToolSchema(
            name="REPL",
            description="Execute Python REPL code.",
            input_schema={"type": "object"},
        )
        return ToolSnapshot(
            schemas=[repl],
            lookup={},
            deferred_names=set(),
            deferred_listing="",
        )


class _Prompts:
    def has(self, key: str) -> bool:
        return key == "orchestrator/repl_tool_surface"

    def render(self, key: str, **kwargs: object) -> str:
        assert key == "orchestrator/repl_tool_surface"
        return (
            "# Current tool surface\n\n"
            f"Direct tool calls available this turn: {kwargs['tool_names']}.\n\n"
            "REPL mode is active. Primitive helpers such as Read, Write, Edit, Glob, "
            "Grep, Bash, PowerShell, Cmd, Python, and Agent are not direct top-level "
            "tool calls in this turn. Use them only as helpers inside REPL code."
        )


@pytest.mark.asyncio
async def test_repl_mode_adds_current_tool_surface_reminder() -> None:
    prompt_builder = _PromptBuilder()
    orchestrator = SimpleNamespace(
        _prompt_builder=prompt_builder,
        _cwd=Path.cwd(),
        _config=SimpleNamespace(
            model=ModelRef(provider="fake", model="fake-model"),
            language=None,
        ),
        _session_id="repl-surface-test",
        _deps=SimpleNamespace(tool_source=_ToolSource(), skills=None, prompts=_Prompts()),
        plan_mode=False,
        _needs_plan_mode_exit_attachment=False,
        _has_exited_plan_mode=False,
    )

    sections, schemas = await build_prompt(orchestrator, "prompt", 2)

    assert prompt_builder.repl_mode is True
    assert [schema.name for schema in schemas] == ["REPL"]
    text = "\n\n".join(section.text for section in sections)
    assert "Direct tool calls available this turn: REPL." in text
    assert "Primitive helpers such as Read, Write" in text
    assert "are not direct top-level tool calls" in text
