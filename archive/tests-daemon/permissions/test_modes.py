"""Tests for permission modes + read-only tool classification."""

from __future__ import annotations

from daemon.permissions.modes import PermissionMode, is_read_only_tool


class TestPermissionMode:
    """Enum value sanity check — values match Claude Code nomenclature."""

    def test_prompt_value(self) -> None:
        assert PermissionMode.PROMPT.value == "default"

    def test_all_values_present(self) -> None:
        values = {m.value for m in PermissionMode}
        assert values == {"default", "accept_edits", "plan", "bypass"}


class TestIsReadOnly:
    """is_read_only_tool coverage."""

    def test_builtin_readonly_tools(self) -> None:
        assert is_read_only_tool("file_read")
        assert is_read_only_tool("glob")
        assert is_read_only_tool("grep")

    def test_case_insensitive(self) -> None:
        assert is_read_only_tool("FILE_READ")
        assert is_read_only_tool("Glob")

    def test_write_tools_rejected(self) -> None:
        assert not is_read_only_tool("bash")
        assert not is_read_only_tool("file_write")
        assert not is_read_only_tool("file_edit")
