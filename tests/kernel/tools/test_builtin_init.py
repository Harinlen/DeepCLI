"""Platform-conditional tool registration in builtin/__init__.py."""

from __future__ import annotations

from unittest.mock import patch

from kernel.tools.builtin.bash import BashTool


def test_bash_tool_on_unix() -> None:
    """On non-Windows, _shell_tool() returns BashTool."""
    with patch("kernel.tools.builtin.selected_shell_tool", return_value="Bash"):
        from kernel.tools.builtin import _shell_tool

        assert _shell_tool() is BashTool


def test_powershell_tool_on_windows() -> None:
    """On Windows, _shell_tool() returns PowerShellTool."""
    with patch("kernel.tools.builtin.selected_shell_tool", return_value="PowerShell"):
        from kernel.tools.builtin import _shell_tool
        from kernel.tools.builtin.powershell import PowerShellTool

        assert _shell_tool() is PowerShellTool


def test_cmd_tool_on_windows_without_powershell() -> None:
    """On Windows without PowerShell, _shell_tool() can return CmdTool."""
    with patch("kernel.tools.builtin.selected_shell_tool", return_value="Cmd"):
        from kernel.tools.builtin import _shell_tool
        from kernel.tools.builtin.cmd import CmdTool

        assert _shell_tool() is CmdTool
