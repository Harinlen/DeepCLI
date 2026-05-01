"""Tests for permission policy."""

from __future__ import annotations

from typing import Any

from daemon.extensions.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.permissions.modes import PermissionMode
from daemon.permissions.policy import needs_permission


class _StubTool(Tool):
    """Stub tool with configurable permission level."""

    name = "stub"
    description = "Stub."
    permission_level = PermissionLevel.NONE

    class Input:
        """Minimal input."""

        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

    def __init__(self, level: PermissionLevel) -> None:
        self.permission_level = level

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output="ok")


class TestNeedsPermission:
    """Tests for the needs_permission function."""

    def test_prompt_mode_none_level(self) -> None:
        """NONE-level tools don't need permission in PROMPT mode."""
        tool = _StubTool(PermissionLevel.NONE)
        assert needs_permission(tool, PermissionMode.PROMPT) is False

    def test_prompt_mode_prompt_level(self) -> None:
        """PROMPT-level tools need permission in PROMPT mode."""
        tool = _StubTool(PermissionLevel.PROMPT)
        assert needs_permission(tool, PermissionMode.PROMPT) is True

    def test_prompt_mode_dangerous_level(self) -> None:
        """DANGEROUS-level tools need permission in PROMPT mode."""
        tool = _StubTool(PermissionLevel.DANGEROUS)
        assert needs_permission(tool, PermissionMode.PROMPT) is True

    def test_bypass_mode_always_false(self) -> None:
        """BYPASS mode never needs permission."""
        for level in PermissionLevel:
            tool = _StubTool(level)
            assert needs_permission(tool, PermissionMode.BYPASS) is False

    def test_accept_edits_mode_allows_file_writes(self) -> None:
        """ACCEPT_EDITS skips prompt for file_write / file_edit."""
        write_tool = _StubTool(PermissionLevel.PROMPT)
        write_tool.name = "file_write"
        assert needs_permission(write_tool, PermissionMode.ACCEPT_EDITS) is False

        edit_tool = _StubTool(PermissionLevel.PROMPT)
        edit_tool.name = "file_edit"
        assert needs_permission(edit_tool, PermissionMode.ACCEPT_EDITS) is False

    def test_accept_edits_mode_still_prompts_other_tools(self) -> None:
        """ACCEPT_EDITS still prompts for bash and other non-edit tools."""
        tool = _StubTool(PermissionLevel.PROMPT)
        tool.name = "bash"
        assert needs_permission(tool, PermissionMode.ACCEPT_EDITS) is True

    def test_default_mode_is_prompt(self) -> None:
        """Default mode argument is PROMPT."""
        tool = _StubTool(PermissionLevel.PROMPT)
        assert needs_permission(tool) is True
