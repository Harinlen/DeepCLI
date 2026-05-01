"""Tests for the tool rule parser."""

from __future__ import annotations

import pytest

from daemon.permissions.rules import ToolRule, matches, parse_rule


class TestParseRule:
    """Tests for parse_rule()."""

    def test_tool_with_pattern(self) -> None:
        """Parse 'Bash(rm *)' into tool_name='bash', pattern='rm *'."""
        rule = parse_rule("Bash(rm *)")
        assert rule == ToolRule(tool_name="bash", pattern="rm *")

    def test_tool_without_pattern(self) -> None:
        """Parse 'Bash' into tool_name='bash', pattern=None."""
        rule = parse_rule("Bash")
        assert rule == ToolRule(tool_name="bash", pattern=None)

    def test_wildcard(self) -> None:
        """Parse '*' into tool_name='*', pattern=None."""
        rule = parse_rule("*")
        assert rule == ToolRule(tool_name="*", pattern=None)

    def test_underscore_name(self) -> None:
        """Parse 'file_read(*.py)' with underscore in name."""
        rule = parse_rule("file_read(*.py)")
        assert rule == ToolRule(tool_name="file_read", pattern="*.py")

    def test_empty_pattern(self) -> None:
        """Parse 'Bash()' into tool_name='bash', pattern=''."""
        rule = parse_rule("Bash()")
        assert rule == ToolRule(tool_name="bash", pattern="")

    def test_case_insensitive(self) -> None:
        """Tool name is lowercased."""
        rule = parse_rule("FILE_READ")
        assert rule.tool_name == "file_read"

    def test_whitespace_stripped(self) -> None:
        """Leading/trailing whitespace is stripped."""
        rule = parse_rule("  Bash(rm *)  ")
        assert rule == ToolRule(tool_name="bash", pattern="rm *")

    def test_empty_string_raises(self) -> None:
        """Empty string raises ValueError."""
        with pytest.raises(ValueError, match="Empty rule"):
            parse_rule("")

    def test_whitespace_only_raises(self) -> None:
        """Whitespace-only string raises ValueError."""
        with pytest.raises(ValueError, match="Empty rule"):
            parse_rule("   ")

    def test_malformed_raises(self) -> None:
        """Malformed rule string raises ValueError."""
        with pytest.raises(ValueError, match="Malformed rule"):
            parse_rule("123invalid")

    def test_complex_pattern(self) -> None:
        """Patterns can contain special characters."""
        rule = parse_rule("Bash(git push --force *)")
        assert rule.pattern == "git push --force *"


class TestMatches:
    """Tests for matches()."""

    def test_exact_tool_name_match(self) -> None:
        """Exact tool name match with no pattern."""
        rule = ToolRule(tool_name="bash", pattern=None)
        assert matches(rule, "bash", {"command": "ls"})

    def test_tool_name_case_insensitive(self) -> None:
        """Tool name matching is case-insensitive."""
        rule = ToolRule(tool_name="bash", pattern=None)
        assert matches(rule, "Bash", {"command": "ls"})

    def test_tool_name_mismatch(self) -> None:
        """Different tool name does not match."""
        rule = ToolRule(tool_name="bash", pattern=None)
        assert not matches(rule, "file_read", {"path": "/etc/passwd"})

    def test_wildcard_matches_any_tool(self) -> None:
        """'*' tool name matches any tool."""
        rule = ToolRule(tool_name="*", pattern=None)
        assert matches(rule, "bash", {"command": "ls"})
        assert matches(rule, "file_read", {"path": "/etc"})

    def test_pattern_matches_first_string_value(self) -> None:
        """Pattern matches against the first string value in input."""
        rule = ToolRule(tool_name="bash", pattern="rm *")
        assert matches(rule, "bash", {"command": "rm -rf /"})

    def test_pattern_no_match(self) -> None:
        """Pattern that doesn't match."""
        rule = ToolRule(tool_name="bash", pattern="rm *")
        assert not matches(rule, "bash", {"command": "ls -la"})

    def test_pattern_no_string_values(self) -> None:
        """Pattern with no string values in input → no match."""
        rule = ToolRule(tool_name="bash", pattern="rm *")
        assert not matches(rule, "bash", {"timeout": 30})

    def test_pattern_empty_input(self) -> None:
        """Pattern with empty input → no match."""
        rule = ToolRule(tool_name="bash", pattern="rm *")
        assert not matches(rule, "bash", {})

    def test_empty_pattern_matches_empty_string(self) -> None:
        """Empty pattern matches empty string value."""
        rule = ToolRule(tool_name="bash", pattern="")
        assert matches(rule, "bash", {"command": ""})

    def test_glob_star(self) -> None:
        """Glob * matches any substring."""
        rule = ToolRule(tool_name="bash", pattern="git *")
        assert matches(rule, "bash", {"command": "git push origin main"})
        assert not matches(rule, "bash", {"command": "echo git"})

    def test_glob_question_mark(self) -> None:
        """Glob ? matches single character."""
        rule = ToolRule(tool_name="file_read", pattern="?.py")
        assert matches(rule, "file_read", {"file_path": "a.py"})
        assert not matches(rule, "file_read", {"file_path": "ab.py"})
