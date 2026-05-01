"""FileRead tool — read file contents with optional offset/limit.

Returns content in ``cat -n`` format (line-numbered).  Detects binary
files and refuses to display them.
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
    ToolDescriptionContext,
    ToolResult,
)
from daemon.extensions.tools.builtin.file_utils import (
    check_binary,
    validate_absolute_path,
    validate_file_exists,
)
from daemon.extensions.tools.image_utils import detect_mime, read_image_as_base64

logger = logging.getLogger(__name__)


class FileReadTool(Tool):
    """Read a file and return its contents with line numbers."""

    name = "file_read"
    description = (
        "Read a file from the filesystem. Returns contents in cat -n format "
        "(with line numbers). Supports offset and limit for partial reads. "
        "The file_path must be an absolute path."
    )

    def get_description(self, ctx: ToolDescriptionContext | None = None) -> str:
        extra = ""
        if ctx and ctx.has_mcp_tools:
            extra += (
                " For files served by an MCP server, prefer the server's "
                "read tools instead (check tool_search)."
            )
        return self.description + extra
    permission_level = PermissionLevel.NONE
    concurrency = ConcurrencyHint.PARALLEL
    max_result_chars = None  # No truncation — avoids persist/read loop

    class Input(BaseModel):
        """Parameters for the file_read tool."""

        file_path: str = Field(description="Absolute path to the file to read.")
        offset: int = Field(
            default=0,
            ge=0,
            description="Line number to start reading from (0-based). Default: 0.",
        )
        limit: int = Field(
            default=2000,
            gt=0,
            description="Maximum number of lines to read. Default: 2000.",
        )

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Read a file and return line-numbered content.

        Args:
            params: Must contain ``file_path``; optionally ``offset``/``limit``.
            ctx: Execution context (unused for reads).

        Returns:
            ToolResult with line-numbered file content, or an error if the
            file doesn't exist or is binary.
        """
        validated = self.Input.model_validate(params)
        path = Path(validated.file_path)

        if err := validate_absolute_path(validated.file_path):
            return err
        if err := validate_file_exists(validated.file_path):
            return err

        # Image branch: magic-byte detect → base64 via image_utils.
        # Runs *before* the binary check because image files legitimately
        # contain null bytes that would otherwise trip it.
        if (mime := detect_mime(path)) is not None:
            try:
                image = read_image_as_base64(path, source_path=validated.file_path)
            except ValueError as exc:
                return ToolResult(output=str(exc), is_error=True)
            except OSError as exc:
                return ToolResult(output=f"Cannot read image: {exc}", is_error=True)
            return ToolResult(
                output=f"[image: {mime}, {len(image.data_base64)} base64 bytes]",
                image_parts=[image],
            )

        if err := check_binary(path):
            return err

        # Read text content
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(output=f"Cannot read file: {exc}", is_error=True)

        lines = text.splitlines()
        total = len(lines)

        # Apply offset + limit
        selected = lines[validated.offset : validated.offset + validated.limit]

        # Format as cat -n (1-based line numbers, tab-separated)
        numbered: list[str] = []
        for i, line in enumerate(selected, start=validated.offset + 1):
            numbered.append(f"{i}\t{line}")

        output = "\n".join(numbered)

        # Append truncation notice if applicable
        remaining = total - (validated.offset + len(selected))
        if remaining > 0:
            output += f"\n\n... ({remaining} more lines, {total} total)"

        if not output:
            output = "(empty file)"

        # Record file state for stale-write detection.
        if ctx.file_state_cache is not None:
            if validated.offset == 0 and remaining == 0:
                ctx.file_state_cache.record_read(validated.file_path)
            else:
                ctx.file_state_cache.record_read_partial(validated.file_path)

        return ToolResult(
            output=output,
            metadata={
                "output_type": "file_content",
                "file_path": validated.file_path,
            },
        )
