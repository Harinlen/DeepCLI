"""Platform detection for shell tool selection."""

from __future__ import annotations

from unittest.mock import patch

from kernel.tools.platform import (
    has_bash,
    has_powershell,
    is_windows,
    use_powershell_tool,
)


class TestIsWindows:
    def test_true_on_win32(self) -> None:
        with patch("kernel.tools.platform.sys") as mock_sys:
            mock_sys.platform = "win32"
            assert is_windows() is True

    def test_false_on_linux(self) -> None:
        with patch("kernel.tools.platform.sys") as mock_sys:
            mock_sys.platform = "linux"
            assert is_windows() is False

    def test_false_on_darwin(self) -> None:
        with patch("kernel.tools.platform.sys") as mock_sys:
            mock_sys.platform = "darwin"
            assert is_windows() is False


class TestHasPowershell:
    def test_true_when_pwsh_found(self) -> None:
        with patch(
            "kernel.tools.platform.shutil.which",
            side_effect=lambda x: "/usr/bin/pwsh" if x == "pwsh" else None,
        ):
            assert has_powershell() is True

    def test_true_when_powershell_found(self) -> None:
        with patch(
            "kernel.tools.platform.shutil.which",
            side_effect=lambda x: (
                "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
                if x == "powershell"
                else None
            ),
        ):
            assert has_powershell() is True

    def test_false_when_neither_found(self) -> None:
        with patch("kernel.tools.platform.shutil.which", return_value=None):
            assert has_powershell() is False


class TestHasBash:
    def test_true_when_bash_found(self) -> None:
        with patch("kernel.tools.platform.shutil.which", return_value="/bin/bash"):
            assert has_bash() is True

    def test_false_when_not_found(self) -> None:
        with patch("kernel.tools.platform.shutil.which", return_value=None):
            assert has_bash() is False


class TestUsePowershellTool:
    """Decision tree tests for ``use_powershell_tool()``."""

    def test_false_on_non_windows(self) -> None:
        """Branch 1: non-Windows always returns False."""
        with patch("kernel.tools.platform.is_windows", return_value=False):
            assert use_powershell_tool() is False

    def test_false_when_env_forces_bash(self) -> None:
        """Branch 2: MUSTANG_USE_BASH=1 forces BashTool."""
        with (
            patch("kernel.tools.platform.is_windows", return_value=True),
            patch.dict("os.environ", {"MUSTANG_USE_BASH": "1"}),
        ):
            assert use_powershell_tool() is False

    def test_false_when_env_forces_bash_true(self) -> None:
        with (
            patch("kernel.tools.platform.is_windows", return_value=True),
            patch.dict("os.environ", {"MUSTANG_USE_BASH": "true"}),
        ):
            assert use_powershell_tool() is False

    def test_false_when_env_forces_bash_yes(self) -> None:
        with (
            patch("kernel.tools.platform.is_windows", return_value=True),
            patch.dict("os.environ", {"MUSTANG_USE_BASH": "yes"}),
        ):
            assert use_powershell_tool() is False

    def test_true_when_powershell_available(self) -> None:
        """Branch 3: Windows + PowerShell found → True."""
        with (
            patch("kernel.tools.platform.is_windows", return_value=True),
            patch.dict("os.environ", {}, clear=True),
            patch("kernel.tools.platform.has_powershell", return_value=True),
        ):
            assert use_powershell_tool() is True

    def test_false_when_no_powershell_but_bash(self) -> None:
        """Branch 4: Windows + no PowerShell + bash found → False (fallback)."""
        with (
            patch("kernel.tools.platform.is_windows", return_value=True),
            patch.dict("os.environ", {}, clear=True),
            patch("kernel.tools.platform.has_powershell", return_value=False),
            patch("kernel.tools.platform.has_bash", return_value=True),
        ):
            assert use_powershell_tool() is False

    def test_true_when_neither_shell_found(self) -> None:
        """Branch 5: Windows + neither found → True (better error from PS tool)."""
        with (
            patch("kernel.tools.platform.is_windows", return_value=True),
            patch.dict("os.environ", {}, clear=True),
            patch("kernel.tools.platform.has_powershell", return_value=False),
            patch("kernel.tools.platform.has_bash", return_value=False),
        ):
            assert use_powershell_tool() is True

    def test_env_zero_does_not_force_bash(self) -> None:
        """``MUSTANG_USE_BASH=0`` is not a valid override."""
        with (
            patch("kernel.tools.platform.is_windows", return_value=True),
            patch.dict("os.environ", {"MUSTANG_USE_BASH": "0"}),
            patch("kernel.tools.platform.has_powershell", return_value=True),
        ):
            assert use_powershell_tool() is True

    def test_env_empty_does_not_force_bash(self) -> None:
        with (
            patch("kernel.tools.platform.is_windows", return_value=True),
            patch.dict("os.environ", {"MUSTANG_USE_BASH": ""}),
            patch("kernel.tools.platform.has_powershell", return_value=True),
        ):
            assert use_powershell_tool() is True
