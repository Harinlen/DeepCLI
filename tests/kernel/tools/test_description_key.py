"""Tool.description_key + PromptManager injection + get_description() hook."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from kernel.orchestrator.types import ToolKind
from kernel.prompts.manager import PromptManager
from kernel.tools.tool import Tool
from kernel.tools.types import ToolCallProgress, ToolCallResult


class _FakeTool(Tool[dict[str, Any], str]):
    name = "FakeTool"
    description = "legacy fallback text"
    description_key = "tools/fake_tool"
    kind = ToolKind.read

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        yield ToolCallResult(data={}, llm_content=[], display=None)  # type: ignore[arg-type]


class _NoKeyTool(Tool[dict[str, Any], str]):
    name = "NoKeyTool"
    description = "inline only"
    kind = ToolKind.read

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        yield ToolCallResult(data={}, llm_content=[], display=None)  # type: ignore[arg-type]


def _pm_with(key: str, text: str) -> PromptManager:
    """Build a PromptManager rooted on a temp dir pre-seeded with one file."""
    root = Path(tempfile.mkdtemp())
    # key "tools/fake_tool" -> <root>/tools/fake_tool.txt
    path = root / f"{key}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    pm = PromptManager(defaults_dir=root)
    pm.load()
    return pm


def test_get_description_resolves_from_prompt_manager() -> None:
    """When description_key is set and PM has the key, description comes from PM."""
    tool = _FakeTool()
    tool._prompt_manager = _pm_with("tools/fake_tool", "PromptManager text wins")
    assert tool.get_description() == "PromptManager text wins"


def test_get_description_falls_back_to_class_var_when_key_missing() -> None:
    """If PM has no matching key, fall back to the ClassVar."""
    tool = _FakeTool()
    tool._prompt_manager = _pm_with("tools/something_else", "unrelated")
    assert tool.get_description() == "legacy fallback text"


def test_get_description_falls_back_when_no_prompt_manager() -> None:
    """No PM injected → use ClassVar (legacy path, test paths)."""
    tool = _FakeTool()
    assert tool._prompt_manager is None
    assert tool.get_description() == "legacy fallback text"


def test_get_description_tool_without_key_always_uses_class_var() -> None:
    """No description_key → never consult PM, even if one is injected."""
    tool = _NoKeyTool()
    tool._prompt_manager = _pm_with("tools/no_key_tool", "should be ignored")
    assert tool.get_description() == "inline only"


def test_to_schema_uses_get_description_result() -> None:
    """to_schema() must route through get_description() so PM text reaches the LLM."""
    tool = _FakeTool()
    tool._prompt_manager = _pm_with("tools/fake_tool", "schema sees this")
    schema = tool.to_schema()
    assert schema.description == "schema sees this"
