"""Bash tool — execute shell commands.

Cross-platform: uses ``bash -c`` on Linux/macOS, ``cmd /c`` on Windows.
Captures stdout + stderr with configurable timeout.
"""

from __future__ import annotations

import platform
import logging
from typing import Any

from pydantic import BaseModel, Field

from daemon.extensions.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolDescriptionContext,
    ToolResult,
)
from daemon.extensions.tools.builtin.bash_safety import (
    get_destructive_warning,
    is_read_only_command,
)
from daemon.extensions.tools.builtin.subprocess_utils import run_with_timeout

logger = logging.getLogger(__name__)

# Default timeout in milliseconds (2 minutes)
_DEFAULT_TIMEOUT_MS = 120_000


class BashTool(Tool):
    """Execute a shell command and return stdout + stderr."""

    name = "bash"
    description = (
        "Execute a shell command and return its output. "
        "The working directory is the project root. "
        "Use ONLY for system commands, build tools, and git operations. "
        "Do NOT use bash for file reading (use file_read), editing (use file_edit), "
        "writing (use file_write), or searching (use glob/grep) — those have "
        "dedicated tools that give the user better visibility. "
        "Set run_in_background=true for long-running commands."
    )

    def get_description(self, ctx: ToolDescriptionContext | None = None) -> str:
        extra = ""
        if ctx and "agent_tool" in ctx.registered_tool_names:
            extra += (
                " For complex multi-step research or exploration, prefer "
                "agent_tool over bash scripts."
            )
        if ctx and ctx.has_mcp_tools:
            extra += (
                " MCP tools may provide higher-level alternatives — "
                "check tool_search first."
            )
        return self.description + extra
    permission_level = PermissionLevel.DANGEROUS

    class Input(BaseModel):
        """Parameters for the bash tool."""

        command: str = Field(min_length=1, description="The shell command to execute.")
        timeout: int | None = Field(
            default=None,
            description="Timeout in milliseconds (max 600000). Default: 120000.",
        )
        run_in_background: bool = Field(
            default=False,
            description="Run in background and return task ID immediately.",
        )
        description: str = Field(
            default="",
            max_length=100,
            description="Short description for progress display.",
        )

    def __init__(self, default_timeout_ms: int = _DEFAULT_TIMEOUT_MS) -> None:
        self._default_timeout_ms = default_timeout_ms

    def get_permission_level(self, params: dict[str, Any]) -> PermissionLevel:
        """Classify the command as read-only (auto-allow) or dangerous.

        Read-only commands (``ls``, ``git status``, ``cat``, etc.)
        return ``NONE`` so the user isn't prompted.  Everything else
        remains ``DANGEROUS``.
        """
        command = params.get("command", "")
        if isinstance(command, str) and is_read_only_command(command):
            return PermissionLevel.NONE
        return PermissionLevel.DANGEROUS

    def get_destructive_warning(self, params: dict[str, Any]) -> str | None:
        """Return a human-readable warning for destructive commands.

        Called by the permission system to enrich the permission prompt.
        Returns ``None`` for non-destructive commands.
        """
        command = params.get("command", "")
        if isinstance(command, str):
            return get_destructive_warning(command)
        return None

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Run a shell command via subprocess.

        Args:
            params: Must contain ``command``; optionally ``timeout``.
            ctx: Provides the working directory.

        Returns:
            ToolResult with combined stdout/stderr, or an error message
            on timeout / non-zero exit.
        """
        validated = self.Input.model_validate(params)
        timeout_ms = (
            validated.timeout if validated.timeout is not None else self._default_timeout_ms
        )
        # Cap at 10 minutes
        timeout_ms = min(timeout_ms, 600_000)
        timeout_s = timeout_ms / 1000.0

        # Background execution: return task ID immediately.
        if validated.run_in_background:
            task_manager = getattr(ctx, "task_manager", None)
            if task_manager is None:
                return ToolResult(
                    output="Background tasks not available in this context.",
                    is_error=True,
                )
            from daemon.tasks.shell_task import TaskManager

            if isinstance(task_manager, TaskManager):
                task_id = await task_manager.spawn(
                    command=validated.command,
                    cwd=ctx.cwd,
                    timeout=int(timeout_s),
                    description=validated.description,
                )
                return ToolResult(
                    output=(
                        f"Background task {task_id} started. "
                        "You will be notified when it completes."
                    )
                )
            return ToolResult(
                output="Background tasks not available in this context.",
                is_error=True,
            )

        shell_cmd = self._build_shell_command(validated.command)

        try:
            result = await run_with_timeout(shell_cmd, cwd=ctx.cwd, timeout_s=timeout_s)
        except OSError as exc:
            return ToolResult(output=f"Failed to start process: {exc}", is_error=True)

        if result.timed_out:
            return ToolResult(
                output=f"Command timed out after {timeout_ms}ms",
                is_error=True,
            )

        # Build output — always include exit code context when non-zero
        parts: list[str] = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(result.stderr)
        output = "\n".join(parts) if parts else "(no output)"

        if result.returncode != 0:
            output = f"Exit code: {result.returncode}\n{output}"

        return ToolResult(
            output=output,
            is_error=result.returncode != 0,
            metadata={
                "output_type": "command_output",
                "exit_code": result.returncode,
            },
        )

    @staticmethod
    def _build_shell_command(command: str) -> list[str]:
        """Build the platform-appropriate shell invocation.

        Returns:
            Argument list for ``create_subprocess_exec``.
        """
        if platform.system() == "Windows":
            return ["cmd", "/c", command]
        return ["bash", "-c", command]
