"""Tests for bash safety — read-only classification and destructive warnings.

Covers:
- is_read_only_command() with simple, compound, and git commands
- get_destructive_warning() pattern matching
- BashTool.get_permission_level() dynamic classification
- BashTool.get_destructive_warning() integration
"""

from __future__ import annotations

import pytest

from daemon.extensions.tools.base import PermissionLevel
from daemon.extensions.tools.builtin.bash_safety import (
    get_destructive_warning,
    is_read_only_command,
)


# ------------------------------------------------------------------
# Read-only classification
# ------------------------------------------------------------------


class TestIsReadOnlyCommand:
    """Tests for is_read_only_command()."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "ls",
            "ls -la",
            "cat foo.txt",
            "head -n 20 file.py",
            "grep -r pattern .",
            "rg pattern",
            "echo hello",
            "pwd",
            "whoami",
            "date",
            "wc -l file.txt",
            "jq '.data' file.json",
            "tree src/",
            "du -sh .",
            "find . -name '*.py'",
        ],
    )
    def test_simple_read_only(self, cmd: str) -> None:
        assert is_read_only_command(cmd) is True

    @pytest.mark.parametrize(
        "cmd",
        [
            "git status",
            "git log --oneline",
            "git diff",
            "git show HEAD",
            "git branch -a",
            "git remote -v",
            "git tag",
            "git rev-parse HEAD",
            "git blame file.py",
        ],
    )
    def test_git_read_only(self, cmd: str) -> None:
        assert is_read_only_command(cmd) is True

    @pytest.mark.parametrize(
        "cmd",
        [
            "rm file.txt",
            "git push origin main",
            "git commit -m 'msg'",
            "git checkout -b new-branch",
            "mkdir new_dir",
            "mv a.txt b.txt",
            "cp a.txt b.txt",
            "python script.py",
            "npm install",
            "pip install requests",
        ],
    )
    def test_not_read_only(self, cmd: str) -> None:
        assert is_read_only_command(cmd) is False

    def test_compound_all_read_only(self) -> None:
        """Pipe of read-only commands → read-only."""
        assert is_read_only_command("cat foo.txt | grep bar") is True

    def test_compound_mixed(self) -> None:
        """Mixed compound → not read-only (one sub-command is dangerous)."""
        assert is_read_only_command("cat foo.txt && rm foo.txt") is False

    def test_compound_and_chain(self) -> None:
        """All read-only in && chain → read-only."""
        assert is_read_only_command("git status && git log --oneline") is True

    def test_empty_command(self) -> None:
        assert is_read_only_command("") is False

    def test_whitespace_only(self) -> None:
        assert is_read_only_command("   ") is False


# ------------------------------------------------------------------
# Destructive warnings
# ------------------------------------------------------------------


class TestGetDestructiveWarning:
    """Tests for get_destructive_warning()."""

    @pytest.mark.parametrize(
        "cmd,expected_fragment",
        [
            ("git push --force origin main", "overwrite remote history"),
            ("git push -f origin main", "overwrite remote history"),
            ("git reset --hard", "discard uncommitted"),
            ("git clean -fd", "permanently delete untracked"),
            ("git checkout .", "discard all working tree"),
            ("git restore .", "discard all working tree"),
            ("git stash drop", "permanently delete stashed"),
            ("git stash clear", "permanently delete stashed"),
            ("git branch -D feature", "force-delete a branch"),
            ("rm -rf /tmp/test", "recursively force-remove"),
            ("rm -r /tmp/test", "recursively remove"),
            ("DROP TABLE users;", "drop or truncate"),
            ("TRUNCATE TABLE orders;", "drop or truncate"),
            ("DELETE FROM users;", "delete all rows"),
            ("kubectl delete pod foo", "delete kubernetes"),
            ("terraform destroy", "destroy infrastructure"),
        ],
    )
    def test_pattern_detected(self, cmd: str, expected_fragment: str) -> None:
        warning = get_destructive_warning(cmd)
        assert warning is not None
        assert expected_fragment in warning.lower()

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push origin main",
            "git status",
            "ls -la",
            "rm file.txt",  # Not recursive, not -f
            "git clean -n",  # Dry run
            "kubectl get pods",
            "terraform plan",
        ],
    )
    def test_no_warning(self, cmd: str) -> None:
        assert get_destructive_warning(cmd) is None

    def test_multiple_patterns(self) -> None:
        """Command matching multiple patterns → combined warning."""
        cmd = "git reset --hard && rm -rf /"
        warning = get_destructive_warning(cmd)
        assert warning is not None
        assert "discard" in warning.lower()
        assert "recursively" in warning.lower()


# ------------------------------------------------------------------
# BashTool.get_permission_level()
# ------------------------------------------------------------------


class TestBashToolDynamicPermission:
    """Tests for BashTool's dynamic permission level."""

    def test_read_only_is_none(self) -> None:
        from daemon.extensions.tools.builtin.bash import BashTool

        tool = BashTool()
        assert tool.get_permission_level({"command": "ls"}) == PermissionLevel.NONE
        assert tool.get_permission_level({"command": "git status"}) == PermissionLevel.NONE

    def test_mutating_is_dangerous(self) -> None:
        from daemon.extensions.tools.builtin.bash import BashTool

        tool = BashTool()
        assert tool.get_permission_level({"command": "rm -rf /"}) == PermissionLevel.DANGEROUS
        assert tool.get_permission_level({"command": "npm install"}) == PermissionLevel.DANGEROUS

    def test_empty_command(self) -> None:
        from daemon.extensions.tools.builtin.bash import BashTool

        tool = BashTool()
        assert tool.get_permission_level({"command": ""}) == PermissionLevel.DANGEROUS

    def test_pipe_all_safe(self) -> None:
        from daemon.extensions.tools.builtin.bash import BashTool

        tool = BashTool()
        level = tool.get_permission_level({"command": "cat file.txt | grep pattern"})
        assert level == PermissionLevel.NONE

    def test_pipe_mixed(self) -> None:
        from daemon.extensions.tools.builtin.bash import BashTool

        tool = BashTool()
        level = tool.get_permission_level({"command": "cat file.txt | tee output.txt"})
        assert level == PermissionLevel.DANGEROUS


class TestBashToolDestructiveWarning:
    """Tests for BashTool.get_destructive_warning()."""

    def test_warning_present(self) -> None:
        from daemon.extensions.tools.builtin.bash import BashTool

        tool = BashTool()
        warning = tool.get_destructive_warning({"command": "git push --force origin main"})
        assert warning is not None
        assert "remote history" in warning

    def test_no_warning(self) -> None:
        from daemon.extensions.tools.builtin.bash import BashTool

        tool = BashTool()
        assert tool.get_destructive_warning({"command": "git push origin main"}) is None


# ------------------------------------------------------------------
# Permission engine integration
# ------------------------------------------------------------------


class TestPermissionEngineWithBash:
    """Verify the permission engine uses dynamic permission levels."""

    def test_read_only_bash_auto_allows(self) -> None:
        from daemon.extensions.tools.builtin.bash import BashTool
        from daemon.permissions.engine import PermissionDecision, PermissionEngine
        from daemon.permissions.settings import PermissionSettings

        engine = PermissionEngine(PermissionSettings())
        tool = BashTool()
        decision = engine.check(tool, {"command": "ls -la"})
        assert decision == PermissionDecision.ALLOW

    def test_dangerous_bash_prompts(self) -> None:
        from daemon.extensions.tools.builtin.bash import BashTool
        from daemon.permissions.engine import PermissionDecision, PermissionEngine
        from daemon.permissions.settings import PermissionSettings

        engine = PermissionEngine(PermissionSettings())
        tool = BashTool()
        decision = engine.check(tool, {"command": "rm -rf /tmp/test"})
        assert decision == PermissionDecision.PROMPT

    def test_git_status_auto_allows(self) -> None:
        from daemon.extensions.tools.builtin.bash import BashTool
        from daemon.permissions.engine import PermissionDecision, PermissionEngine
        from daemon.permissions.settings import PermissionSettings

        engine = PermissionEngine(PermissionSettings())
        tool = BashTool()
        decision = engine.check(tool, {"command": "git status"})
        assert decision == PermissionDecision.ALLOW

    def test_git_push_prompts(self) -> None:
        from daemon.extensions.tools.builtin.bash import BashTool
        from daemon.permissions.engine import PermissionDecision, PermissionEngine
        from daemon.permissions.settings import PermissionSettings

        engine = PermissionEngine(PermissionSettings())
        tool = BashTool()
        decision = engine.check(tool, {"command": "git push origin main"})
        assert decision == PermissionDecision.PROMPT
