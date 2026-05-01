"""Grep tool — search file contents by regex.

Prefers ``rg`` (ripgrep) for speed; falls back to ``grep -rn`` if
rg is not installed.  Supports glob filtering and multiple output modes.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from daemon.extensions.tools.base import (
    ConcurrencyHint,
    PermissionLevel,
    Tool,
    ToolContext,
    ToolDescriptionContext,
    ToolResult,
)
from daemon.extensions.tools.builtin.subprocess_utils import run_with_timeout

logger = logging.getLogger(__name__)

# Limit output to avoid flooding the LLM context
_MAX_OUTPUT_LINES = 500


class GrepTool(Tool):
    """Search file contents using regex (ripgrep or grep fallback)."""

    name = "grep"
    description = (
        "Search file contents for a regex pattern. Uses ripgrep (rg) if "
        "available, otherwise falls back to grep. Supports glob filtering "
        "and output mode selection."
    )

    def get_description(self, ctx: ToolDescriptionContext | None = None) -> str:
        extra = ""
        if ctx and "agent_tool" in ctx.registered_tool_names:
            extra += (
                " For broad codebase exploration requiring multiple "
                "search rounds, prefer agent_tool with an exploration prompt."
            )
        return self.description + extra
    permission_level = PermissionLevel.NONE
    concurrency = ConcurrencyHint.PARALLEL

    class Input(BaseModel):
        """Parameters for the grep tool."""

        pattern: str = Field(min_length=1, description="Regex pattern to search for.")
        path: str | None = Field(
            default=None,
            description="File or directory to search in. Defaults to cwd.",
        )
        glob: str | None = Field(
            default=None,
            description="Glob pattern to filter files (e.g. '*.py').",
        )
        output_mode: Literal["content", "files_with_matches", "count"] = Field(
            default="content",
            description=(
                "Output mode: 'content' (matching lines), "
                "'files_with_matches' (file paths only), "
                "'count' (match counts)."
            ),
        )

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Search files for the regex pattern.

        Args:
            params: Must contain ``pattern``; optionally ``path``,
                ``glob``, ``output_mode``.
            ctx: Provides the default working directory.

        Returns:
            ToolResult with search results or an error message.
        """
        validated = self.Input.model_validate(params)
        search_path = validated.path or ctx.cwd

        # Verify search path exists
        if not Path(search_path).exists():
            return ToolResult(
                output=f"Path not found: {search_path}",
                is_error=True,
            )

        cmd = self._build_command(
            pattern=validated.pattern,
            path=search_path,
            glob_filter=validated.glob,
            output_mode=validated.output_mode,
        )

        if cmd is None:
            return ToolResult(
                output="Neither rg nor grep found on this system.",
                is_error=True,
            )

        try:
            result = await run_with_timeout(cmd, cwd=ctx.cwd, timeout_s=30.0)
        except OSError as exc:
            return ToolResult(output=f"Failed to run search: {exc}", is_error=True)

        if result.timed_out:
            return ToolResult(output="Search timed out after 30s", is_error=True)

        # rg/grep return exit code 1 for "no matches" — not an error
        if result.returncode not in (0, 1):
            return ToolResult(
                output=f"Search failed: {result.stderr or result.stdout}",
                is_error=True,
            )

        if not result.stdout.strip():
            return ToolResult(output="No matches found.")

        # Truncate output
        lines = result.stdout.splitlines()
        if len(lines) > _MAX_OUTPUT_LINES:
            lines = lines[:_MAX_OUTPUT_LINES]
            lines.append(f"\n... (truncated to {_MAX_OUTPUT_LINES} lines)")

        return ToolResult(
            output="\n".join(lines),
            metadata={"output_type": "command_output"},
        )

    @staticmethod
    def _build_command(
        pattern: str,
        path: str,
        glob_filter: str | None,
        output_mode: str,
    ) -> list[str] | None:
        """Build the rg or grep command.

        Returns:
            Argument list, or ``None`` if no search tool is found.
        """
        rg_path = shutil.which("rg")
        if rg_path:
            cmd = [rg_path, "--no-heading", "-n"]

            if output_mode == "files_with_matches":
                cmd.append("-l")
            elif output_mode == "count":
                cmd.append("-c")

            if glob_filter:
                cmd.extend(["--glob", glob_filter])

            cmd.extend([pattern, path])
            return cmd

        # Fallback to grep
        grep_path = shutil.which("grep")
        if grep_path:
            cmd = [grep_path, "-rn"]

            if output_mode == "files_with_matches":
                cmd.append("-l")
            elif output_mode == "count":
                cmd.append("-c")

            if glob_filter:
                cmd.extend(["--include", glob_filter])

            cmd.extend([pattern, path])
            return cmd

        return None
