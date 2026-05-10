"""Shared constants for the ToolAuthorizer subsystem."""

from __future__ import annotations

from typing import Final

BASH_TOOL_NAME: Final = "Bash"
"""Primary name of the Bash tool.  The BashClassifier (LLMJudge) is
triggered when an ``authorize()`` call sees a Tool with this exact
name.  Aligns with Claude Code's approach — string equality rather
than isinstance / class flag."""

POWERSHELL_TOOL_NAME: Final = "PowerShell"
"""Primary name of the PowerShell tool (Windows counterpart of Bash)."""

SHELL_TOOL_NAMES: Final = frozenset({BASH_TOOL_NAME, POWERSHELL_TOOL_NAME})
"""Both shell tool names.  Used by the authorizer to trigger the
BashClassifier for either shell tool — the LLM judge prompt is
shell-agnostic."""


__all__ = ["BASH_TOOL_NAME", "POWERSHELL_TOOL_NAME", "SHELL_TOOL_NAMES"]
