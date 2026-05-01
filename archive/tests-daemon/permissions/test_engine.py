"""Tests for PermissionEngine — rule + mode + tool-level decisions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from daemon.extensions.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.permissions.engine import PermissionDecision, PermissionEngine
from daemon.permissions.modes import PermissionMode
from daemon.permissions.settings import PermissionSettings


class _StubTool(Tool):
    """Stub with configurable name + permission level."""

    name = "stub"
    description = "Stub."
    permission_level = PermissionLevel.NONE

    class Input:
        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

    def __init__(
        self,
        name: str = "stub",
        level: PermissionLevel = PermissionLevel.PROMPT,
    ) -> None:
        self.name = name
        self.permission_level = level

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output="ok")


def _engine(tmp_path: Path, mode: PermissionMode = PermissionMode.PROMPT) -> PermissionEngine:
    settings = PermissionSettings(tmp_path / "settings.json")
    return PermissionEngine(settings=settings, mode=mode)


class TestMemoryProtection:
    """Hardcoded deny on ~/.mustang/memory/ for file_edit / file_write (D17)."""

    def test_denies_file_edit_tilde_path(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.BYPASS)
        tool = _StubTool("file_edit", PermissionLevel.NONE)
        assert (
            eng.check(tool, {"file_path": "~/.mustang/memory/user/role.md"})
            == PermissionDecision.DENY
        )

    def test_denies_file_write_absolute(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.BYPASS)
        tool = _StubTool("file_write", PermissionLevel.NONE)
        home = str(Path.home())
        assert (
            eng.check(tool, {"file_path": f"{home}/.mustang/memory/feedback/x.md"})
            == PermissionDecision.DENY
        )

    def test_allows_file_read_on_memory(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.PROMPT)
        tool = _StubTool("file_read", PermissionLevel.NONE)
        assert (
            eng.check(tool, {"file_path": "~/.mustang/memory/user/role.md"})
            == PermissionDecision.ALLOW
        )

    def test_allows_other_paths(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.BYPASS)
        tool = _StubTool("file_edit", PermissionLevel.NONE)
        assert eng.check(tool, {"file_path": "/tmp/elsewhere.md"}) == PermissionDecision.ALLOW

    def test_guardrail_runs_before_bypass(self, tmp_path: Path) -> None:
        """Even BYPASS mode can't touch memory/."""
        eng = _engine(tmp_path, PermissionMode.BYPASS)
        tool = _StubTool("file_edit", PermissionLevel.NONE)
        assert (
            eng.check(tool, {"file_path": "~/.mustang/memory/project/x.md"})
            == PermissionDecision.DENY
        )


class TestModes:
    """Mode-level decisions."""

    def test_bypass_allows_everything(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.BYPASS)
        tool = _StubTool("bash", PermissionLevel.DANGEROUS)
        assert eng.check(tool, {"command": "rm -rf /"}) == PermissionDecision.ALLOW

    def test_prompt_mode_with_none_level_allows(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.PROMPT)
        tool = _StubTool("grep", PermissionLevel.NONE)
        assert eng.check(tool, {}) == PermissionDecision.ALLOW

    def test_prompt_mode_with_prompt_level_prompts(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.PROMPT)
        tool = _StubTool("bash", PermissionLevel.PROMPT)
        assert eng.check(tool, {"command": "ls"}) == PermissionDecision.PROMPT

    def test_accept_edits_allows_file_writes(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.ACCEPT_EDITS)
        tool = _StubTool("file_write", PermissionLevel.PROMPT)
        assert eng.check(tool, {"file_path": "/x.py"}) == PermissionDecision.ALLOW

    def test_accept_edits_still_prompts_bash(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.ACCEPT_EDITS)
        tool = _StubTool("bash", PermissionLevel.PROMPT)
        assert eng.check(tool, {"command": "ls"}) == PermissionDecision.PROMPT


class TestPlanMode:
    """PLAN mode: read-only tools + plan-file edits only."""

    def test_plan_mode_allows_readonly(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.PLAN)
        assert eng.check(_StubTool("file_read"), {"file_path": "x"}) == PermissionDecision.ALLOW
        assert eng.check(_StubTool("glob"), {"pattern": "*"}) == PermissionDecision.ALLOW
        assert eng.check(_StubTool("grep"), {"pattern": "x"}) == PermissionDecision.ALLOW

    def test_plan_mode_denies_bash(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.PLAN)
        assert eng.check(_StubTool("bash"), {"command": "ls"}) == PermissionDecision.DENY

    def test_plan_mode_denies_file_write_without_plan_file(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.PLAN)
        assert eng.check(_StubTool("file_write"), {"file_path": "/x.py"}) == PermissionDecision.DENY

    def test_plan_mode_allows_plan_file_edit(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.PLAN)
        plan = tmp_path / "abc.plan.md"
        eng.set_plan_file(str(plan))
        assert (
            eng.check(_StubTool("file_write"), {"file_path": str(plan)}) == PermissionDecision.ALLOW
        )

    def test_plan_mode_denies_wrong_plan_file(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.PLAN)
        eng.set_plan_file(str(tmp_path / "abc.plan.md"))
        assert (
            eng.check(_StubTool("file_edit"), {"file_path": str(tmp_path / "xyz.plan.md")})
            == PermissionDecision.DENY
        )

    def test_plan_mode_allows_control_tools(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.PLAN)
        assert eng.check(_StubTool("enter_plan_mode"), {}) == PermissionDecision.ALLOW
        assert eng.check(_StubTool("exit_plan_mode"), {"plan": "..."}) == PermissionDecision.ALLOW


class TestRules:
    """allow / deny rule matching."""

    def test_allow_rule_short_circuits_prompt(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.PROMPT)
        eng.settings.add_allow_rule("Bash(git *)")
        tool = _StubTool("bash", PermissionLevel.PROMPT)
        assert eng.check(tool, {"command": "git status"}) == PermissionDecision.ALLOW

    def test_deny_rule_beats_allow(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.PROMPT)
        eng.settings.add_allow_rule("bash")
        eng.settings.add_deny_rule("Bash(rm -rf *)")
        tool = _StubTool("bash", PermissionLevel.PROMPT)
        assert eng.check(tool, {"command": "rm -rf /tmp/x"}) == PermissionDecision.DENY

    def test_no_rules_falls_to_mode(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path, PermissionMode.PROMPT)
        tool = _StubTool("bash", PermissionLevel.PROMPT)
        assert eng.check(tool, {"command": "git status"}) == PermissionDecision.PROMPT


class TestDenialTracking:
    """Consecutive-denial counter."""

    def test_counter_increments(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        assert eng.record_denial("bash") == 1
        assert eng.record_denial("bash") == 2
        assert eng.record_denial("bash") == 3

    def test_counter_separate_per_tool(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        eng.record_denial("bash")
        assert eng.record_denial("file_write") == 1

    def test_record_allow_resets(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        eng.record_denial("bash")
        eng.record_denial("bash")
        eng.record_allow("bash")
        assert eng.record_denial("bash") == 1


class TestRuleGeneration:
    """generate_rule_for_tool heuristics."""

    def test_bash_extracts_first_token(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        assert eng.generate_rule_for_tool("bash", {"command": "git status"}) == "Bash(git *)"

    def test_bash_empty_command_falls_back(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        assert eng.generate_rule_for_tool("bash", {"command": ""}) == "Bash"

    def test_file_write_uses_parent_glob(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        rule = eng.generate_rule_for_tool("file_write", {"file_path": "src/foo/bar.py"})
        assert rule == "file_write(src/foo/**)"

    def test_file_write_root(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        # A bare file name has no parent directory.
        rule = eng.generate_rule_for_tool("file_write", {"file_path": "foo.py"})
        assert rule == "file_write(**)"

    def test_generic_tool_uses_bare_name(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        assert eng.generate_rule_for_tool("grep", {"pattern": "x"}) == "grep"


class TestPermissionResponseRoundTrip:
    """Integration: always_allow persists a rule."""

    @pytest.mark.asyncio
    async def test_always_allow_persists(self, tmp_path: Path) -> None:
        settings = PermissionSettings(tmp_path / "settings.json")
        eng = PermissionEngine(settings=settings)
        suggested = eng.generate_rule_for_tool("bash", {"command": "git pull"})
        settings.add_allow_rule(suggested)

        # Second check should now ALLOW.
        tool = _StubTool("bash", PermissionLevel.PROMPT)
        assert eng.check(tool, {"command": "git fetch"}) == PermissionDecision.ALLOW
