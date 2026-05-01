"""Cmd — execute a Windows cmd.exe command and capture output."""

from __future__ import annotations

import re
import shutil
from collections.abc import AsyncGenerator
from typing import Any

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.builtin.shell_exec import ShellSpec, run_shell_command
from kernel.tools.context import ToolContext
from kernel.tools.tool import RiskContext, Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)


ALLOWLIST_SAFE_COMMANDS: frozenset[str] = frozenset(
    {
        "cd",
        "dir",
        "echo",
        "set",
        "type",
        "ver",
        "vol",
        "where",
        "whoami",
        "git",
        "python",
        "py",
        "node",
        "npm",
        "pnpm",
        "yarn",
    }
)

DANGEROUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(del|erase)\s+.*[/\\]", re.IGNORECASE),
    re.compile(r"\b(rmdir|rd)\s+(/s|/q)", re.IGNORECASE),
    re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE),
    re.compile(r"\bdiskpart\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breg\s+delete\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\s+.*--force\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\s+.*-f\b(?!orce-with-lease)", re.IGNORECASE),
)

COMPOUND_TOKENS: tuple[str, ...] = ("&", "&&", "||", "|", ">")


class CmdTool(Tool[dict[str, Any], str]):
    """Execute a command through ``cmd.exe /d /s /c``."""

    name = "Cmd"
    description = "Execute a Windows cmd.exe command."
    aliases = ("Bash", "PowerShell")
    kind = ToolKind.execute
    interrupt_behavior = "cancel"

    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The cmd.exe command to execute."},
            "timeout_ms": {
                "type": "integer",
                "description": "Kill the command after this many ms. Default 120000.",
            },
        },
        "required": ["command"],
    }

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        command = str(input.get("command", ""))
        if not command.strip():
            return PermissionSuggestion("medium", "ask", "empty command")
        for pattern in DANGEROUS_PATTERNS:
            if pattern.search(command):
                return PermissionSuggestion("high", "deny", f"matches dangerous pattern {pattern.pattern!r}")
        if any(token in command for token in COMPOUND_TOKENS):
            return PermissionSuggestion("medium", "ask", "compound command needs review")
        head = command.strip().split(None, 1)[0].lower()
        if head in ALLOWLIST_SAFE_COMMANDS:
            return PermissionSuggestion("low", "allow", f"safe allowlist: {head!r}")
        return PermissionSuggestion("medium", "ask", f"unclassified command: {head!r}")

    def prepare_permission_matcher(self, input: dict[str, Any]):  # noqa: ANN201
        command = str(input.get("command", "")).lower()

        def matcher(pattern: str) -> bool:
            if pattern.endswith(":*"):
                prefix = pattern[:-2].rstrip().lower()
                return command == prefix or command.startswith(prefix + " ")
            return command == pattern.lower()

        return matcher

    def is_destructive(self, input: dict[str, Any]) -> bool:
        command = str(input.get("command", ""))
        return any(pattern.search(command) for pattern in DANGEROUS_PATTERNS)

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
        cmd = shutil.which("cmd.exe") or shutil.which("cmd")
        if cmd is None:
            error = "cmd.exe not found on PATH"
            yield ToolCallResult(
                data={"exit_code": -1, "stdout": "", "stderr": error},
                llm_content=[TextBlock(type="text", text=error)],
                display=TextDisplay(text=error),
            )
            return

        async for event in run_shell_command(
            ShellSpec(argv=[cmd, "/d", "/s", "/c", command]),
            cwd=ctx.cwd,
            env=ctx.env,
            timeout_ms=timeout_ms,
        ):
            yield event


__all__ = ["ALLOWLIST_SAFE_COMMANDS", "COMPOUND_TOKENS", "DANGEROUS_PATTERNS", "CmdTool"]
