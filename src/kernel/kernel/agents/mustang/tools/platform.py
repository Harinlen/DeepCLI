"""Platform detection for tool registration.

Used by ``builtin/__init__.py`` to select BashTool (Unix) or
PowerShellTool (Windows) at startup.  Mirrors Claude Code's
``src/utils/shell/shellToolUtils.ts:isPowerShellToolEnabled``.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys

logger = logging.getLogger(__name__)


def is_windows() -> bool:
    """True when running on Windows (including Cygwin)."""
    return sys.platform == "win32"


def has_powershell() -> bool:
    """True when ``pwsh`` or ``powershell`` is on PATH."""
    return shutil.which("pwsh") is not None or shutil.which("powershell") is not None


def has_bash() -> bool:
    """True when ``bash`` is on PATH."""
    return shutil.which("bash") is not None


def has_cmd() -> bool:
    """True when ``cmd.exe`` or ``cmd`` is on PATH."""
    return shutil.which("cmd.exe") is not None or shutil.which("cmd") is not None


def selected_shell_tool() -> str:
    """Return the built-in shell tool name selected for this platform."""
    if not is_windows():
        return "Bash"
    if os.environ.get("MUSTANG_USE_BASH", "").strip().lower() in ("1", "true", "yes"):
        return "Bash"
    if has_powershell():
        return "PowerShell"
    if has_cmd():
        return "Cmd"
    if has_bash():
        logger.warning(
            "PowerShell and cmd.exe not found on PATH; falling back to BashTool "
            "(bash found — likely WSL or Git Bash environment)"
        )
        return "Bash"
    return "Cmd"


def use_powershell_tool() -> bool:
    """Whether PowerShellTool should replace BashTool.

    Decision tree:

    1. Non-Windows → always ``False`` (use BashTool).
    2. ``MUSTANG_USE_BASH=1`` → ``False`` (user forced BashTool).
    3. PowerShell binary found → ``True``.
    4. PowerShell not found, bash found → ``False`` (fallback).
    5. Neither found → ``True`` (register PowerShellTool; it will
       report a clear error at ``call()`` time rather than silently
       registering a BashTool that also can't run).
    """
    if selected_shell_tool() == "PowerShell":
        return True
    # Backward-compatible predicate semantics for callers/tests that ask
    # specifically whether the old PowerShellTool fallback would be used.
    return is_windows() and not has_powershell() and not has_cmd() and not has_bash()


__all__ = [
    "has_bash",
    "has_cmd",
    "has_powershell",
    "is_windows",
    "selected_shell_tool",
    "use_powershell_tool",
]
