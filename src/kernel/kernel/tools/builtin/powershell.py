"""PowerShell — execute a PowerShell command and capture output.

Windows counterpart of ``bash.py``.  Mirrors Claude Code's
``src/tools/PowerShellTool/PowerShellTool.tsx`` + supporting modules.

Domain knowledge (allowlist, dangerous patterns, permission matching)
is fully independent from BashTool — Windows and Unix security models
diverge enough that sharing would paper over real gaps.

On Windows, ``PowerShellTool`` replaces ``BashTool`` entirely in the
registry.  The ``aliases = ("Bash",)`` declaration ensures backward
compatibility: permission rules written as ``Bash(git:*)`` and LLM
tool calls targeting ``"Bash"`` resolve to this tool via
``matches_name``.
"""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Any

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.context import ToolContext
from kernel.tools.builtin.shell_exec import ShellSpec, run_shell_command
from kernel.tools.tool import RiskContext, Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Safe cmdlets / executables — auto-allow without prompting
# ──────────────────────────────────────────────────────────────────
# Stored lowercase; ``_cmdlet_head`` normalises to lowercase before
# comparison so matching is case-insensitive.

ALLOWLIST_SAFE_CMDLETS: frozenset[str] = frozenset(
    {
        # PowerShell cmdlets
        "get-childitem",
        "get-location",
        "get-content",
        "get-item",
        "get-itemproperty",
        "select-string",
        "test-path",
        "resolve-path",
        "get-date",
        "get-host",
        "get-process",
        "get-service",
        "get-filehash",
        "get-acl",
        "measure-object",
        "select-object",
        "where-object",
        "format-table",
        "format-list",
        "format-hex",
        "out-string",
        "write-output",
        "write-host",
        "get-command",
        "get-help",
        "get-module",
        "get-alias",
        # Common aliases that resolve to safe cmdlets
        "ls",
        "dir",
        "cat",
        "type",
        "pwd",
        "echo",
        # Cross-platform executables (same subset as BashTool)
        "git",
        "python",
        "python3",
        "uv",
        "node",
        "npm",
        "pnpm",
        "cargo",
        "go",
        "make",
        "pytest",
        "ruff",
        "mypy",
        "tsc",
        "yarn",
    }
)


# ──────────────────────────────────────────────────────────────────
# Dangerous patterns — force ``deny`` regardless of first token
# ───────────────────────────────────────────────────────���──────────
# All compiled with ``re.IGNORECASE`` because PowerShell cmdlets are
# case-insensitive.

DANGEROUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Code execution / eval
    re.compile(r"\bInvoke-Expression\b", re.IGNORECASE),
    re.compile(r"\biex\b", re.IGNORECASE),
    # Privilege escalation
    re.compile(r"\bStart-Process\b.*-Verb\s+RunAs", re.IGNORECASE),
    # Download cradles
    re.compile(r"\bNew-Object\s+.*Net\.WebClient\b", re.IGNORECASE),
    re.compile(r"\bInvoke-WebRequest\b.*\|\s*Invoke-Expression", re.IGNORECASE),
    re.compile(r"\bInvoke-RestMethod\b.*\|\s*iex\b", re.IGNORECASE),
    # Destructive file operations
    re.compile(r"\bRemove-Item\s+.*-Recurse\b.*[/\\]", re.IGNORECASE),
    # Disk / system
    re.compile(r"\bFormat-Volume\b", re.IGNORECASE),
    re.compile(r"\bClear-Disk\b", re.IGNORECASE),
    re.compile(r"\bStop-Computer\b", re.IGNORECASE),
    re.compile(r"\bRestart-Computer\b", re.IGNORECASE),
    # Execution policy bypass
    re.compile(r"\bSet-ExecutionPolicy\s+Unrestricted\b", re.IGNORECASE),
    re.compile(r"-ExecutionPolicy\s+Bypass\b", re.IGNORECASE),
    # Git force push (same as BashTool)
    re.compile(r"\bgit\s+push\s+.*--force\b"),
    re.compile(r"\bgit\s+push\s+.*-f\b(?!orce-with-lease)"),
)


COMPOUND_TOKENS: tuple[str, ...] = (";", "|", "&&", "||", "$(", "`")
"""Tokens that indicate a compound / pipeline command; classification
gets harder so we escalate to ``ask``."""


# ──────────────────────────────────────────────────────────────────
# Tool implementation
# ──────────────────────────────────────────────────────────────────


class PowerShellTool(Tool[dict[str, Any], str]):
    """Execute a PowerShell command and capture stdout + stderr.

    Registered instead of ``BashTool`` on Windows.  The ``"Bash"``
    alias ensures backward compatibility with existing permission
    rules and LLM tool calls.
    """

    name = "PowerShell"
    description_key = "tools/powershell"
    description = "Execute a PowerShell command."
    aliases = ("Bash",)
    kind = ToolKind.execute
    interrupt_behavior = "cancel"

    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The PowerShell command to execute.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Kill the command after this many ms. Default 120000.",
            },
        },
        "required": ["command"],
    }

    # ------------------------------------------------------------------
    # Information source for ToolAuthorizer
    # ------------------------------------------------------------------

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        command = input.get("command", "")
        if not isinstance(command, str) or not command.strip():
            return PermissionSuggestion(
                risk="medium", default_decision="ask", reason="empty command"
            )

        # Dangerous-pattern match wins over allowlist.
        for pattern in DANGEROUS_PATTERNS:
            if pattern.search(command):
                return PermissionSuggestion(
                    risk="high",
                    default_decision="deny",
                    reason=f"matches dangerous pattern {pattern.pattern!r}",
                )

        # Compound / pipeline commands need semantic review.
        for token in COMPOUND_TOKENS:
            if token in command:
                return PermissionSuggestion(
                    risk="medium",
                    default_decision="ask",
                    reason="compound command needs review",
                )

        head = _cmdlet_head(command)
        if head is None:
            return PermissionSuggestion(
                risk="medium",
                default_decision="ask",
                reason="could not parse cmdlet",
            )
        if head in ALLOWLIST_SAFE_CMDLETS:
            return PermissionSuggestion(
                risk="low",
                default_decision="allow",
                reason=f"safe allowlist: {head!r}",
            )
        return PermissionSuggestion(
            risk="medium",
            default_decision="ask",
            reason=f"unclassified cmdlet: {head!r}",
        )

    def prepare_permission_matcher(self, input: dict[str, Any]):  # noqa: ANN201
        """Case-insensitive prefix matching for permission rules.

        PowerShell cmdlets are case-insensitive, so ``Get-Content`` and
        ``get-content`` must both match a rule like ``Get-Content:*``.
        """
        command = str(input.get("command", "")).lower()

        def matcher(pattern: str) -> bool:
            if pattern.endswith(":*"):
                prefix = pattern[:-2].rstrip().lower()
                return command == prefix or command.startswith(prefix + " ")
            return command == pattern.lower()

        return matcher

    def is_destructive(self, input: dict[str, Any]) -> bool:
        command = str(input.get("command", ""))
        return any(p.search(command) for p in DANGEROUS_PATTERNS)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def validate_input(self, input: dict[str, Any], ctx: RiskContext) -> None:
        command = input.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolInputError("command must be a non-empty string")
        if len(command) > 32_000:
            raise ToolInputError("command exceeds 32,000 character limit")

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        command = input["command"]
        timeout_ms = int(input.get("timeout_ms") or 120_000)

        pwsh = _resolve_pwsh_binary()
        if pwsh is None:
            error = "neither pwsh nor powershell found on PATH"
            yield ToolCallResult(
                data={"exit_code": -1, "stdout": "", "stderr": error},
                llm_content=[TextBlock(type="text", text=error)],
                display=TextDisplay(text=error),
            )
            return

        async for event in run_shell_command(
            ShellSpec(argv=[pwsh, "-NoProfile", "-NonInteractive", "-Command", command]),
            cwd=ctx.cwd,
            env=ctx.env,
            timeout_ms=timeout_ms,
        ):
            yield event


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _cmdlet_head(command: str) -> str | None:
    r"""Return the first token of a PowerShell command, lowercased.

    Handles the ``&`` call operator (``& Get-Process``) and ``.\``
    relative-path prefix (``.\script.ps1``).
    """
    stripped = command.strip()
    if not stripped:
        return None

    tokens = stripped.split(None, 2)
    first = tokens[0]

    # ``& <cmdlet>`` — call operator; the real command is the second token.
    if first == "&" and len(tokens) >= 2:
        first = tokens[1]

    # Strip leading `.\` or `./` (common Windows invocation pattern).
    if first.startswith((".\\", "./")):
        first = first[2:]

    return first.lower() if first else None


@lru_cache(maxsize=1)
def _resolve_pwsh_binary() -> str | None:
    """Find the PowerShell executable, preferring PS 7 (``pwsh``)."""
    return shutil.which("pwsh") or shutil.which("powershell")


__all__ = [
    "ALLOWLIST_SAFE_CMDLETS",
    "COMPOUND_TOKENS",
    "DANGEROUS_PATTERNS",
    "PowerShellTool",
]
