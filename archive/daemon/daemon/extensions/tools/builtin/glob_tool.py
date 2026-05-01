"""Glob tool — find files by pattern.

Uses Python's ``pathlib.Path.glob()`` for pattern matching, returns
results sorted by modification time (newest first).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from daemon.extensions.tools.base import (
    ConcurrencyHint,
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)

logger = logging.getLogger(__name__)

# Hard cap on results to avoid flooding the LLM context
_MAX_RESULTS = 500


class GlobTool(Tool):
    """Find files matching a glob pattern."""

    name = "glob"
    description = (
        "Find files matching a glob pattern (e.g. '**/*.py', 'src/**/*.ts'). "
        "Returns matching paths sorted by modification time (newest first). "
        "If path is not specified, uses the current working directory."
    )
    permission_level = PermissionLevel.NONE
    concurrency = ConcurrencyHint.PARALLEL

    class Input(BaseModel):
        """Parameters for the glob tool."""

        pattern: str = Field(description="Glob pattern to match files against.")
        path: str | None = Field(
            default=None,
            description="Directory to search in. Defaults to cwd.",
        )

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Find files matching the glob pattern.

        Args:
            params: Must contain ``pattern``; optionally ``path``.
            ctx: Provides the default working directory.

        Returns:
            ToolResult listing matched file paths, one per line.
        """
        validated = self.Input.model_validate(params)
        search_dir = Path(validated.path) if validated.path else Path(ctx.cwd)

        if not search_dir.is_dir():
            return ToolResult(
                output=f"Directory not found: {search_dir}",
                is_error=True,
            )

        try:
            matches = list(search_dir.glob(validated.pattern))
        except ValueError as exc:
            # Invalid glob pattern
            return ToolResult(output=f"Invalid glob pattern: {exc}", is_error=True)

        # Filter to files only, get mtime for sorting.
        # Guard stat() — files may vanish between glob and sort.
        timed: list[tuple[float, Path]] = []
        for m in matches:
            try:
                st = m.stat()
                if not m.is_file():
                    continue
                timed.append((st.st_mtime, m))
            except OSError:
                continue
        timed.sort(key=lambda t: t[0], reverse=True)
        files = [p for _, p in timed]

        if not files:
            return ToolResult(output="No files matched the pattern.")

        # Truncate to cap
        truncated = len(files) > _MAX_RESULTS
        files = files[:_MAX_RESULTS]

        output = "\n".join(str(f) for f in files)
        if truncated:
            output += f"\n\n... (truncated to {_MAX_RESULTS} results)"

        return ToolResult(
            output=output,
            metadata={"output_type": "command_output"},
        )
