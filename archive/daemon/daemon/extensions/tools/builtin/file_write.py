"""FileWrite tool — create or overwrite a file.

Auto-creates parent directories.  The orchestrator should track which
files have been read so that accidental overwrites can be prevented
(enforced at the orchestrator level, not here).
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
from daemon.extensions.tools.builtin.file_utils import validate_absolute_path
from daemon.side_effects import FileChanged

logger = logging.getLogger(__name__)


class FileWriteTool(Tool):
    """Write content to a file, creating parent directories as needed."""

    name = "file_write"
    description = (
        "Write content to a file. Creates parent directories if needed. "
        "Overwrites the file if it exists. "
        "The file_path must be an absolute path."
    )
    permission_level = PermissionLevel.PROMPT
    concurrency = ConcurrencyHint.KEYED

    class Input(BaseModel):
        """Parameters for the file_write tool."""

        file_path: str = Field(description="Absolute path to the file to write.")
        content: str = Field(description="The content to write to the file.")

    def concurrency_key(self, params: dict[str, Any]) -> str | None:
        """Serialize writes to the same file."""
        return params.get("file_path")

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Write content to a file.

        Args:
            params: Must contain ``file_path`` and ``content``.
            ctx: Execution context (unused for writes).

        Returns:
            ToolResult confirming the write, or an error message.
        """
        validated = self.Input.model_validate(params)
        path = Path(validated.file_path)

        if err := validate_absolute_path(validated.file_path):
            return err

        # Stale-write check: reject if existing file modified since last read.
        if ctx.file_state_cache is not None:
            ok, msg = ctx.file_state_cache.check_before_write(validated.file_path)
            if not ok:
                return ToolResult(output=msg, is_error=True)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(validated.content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(output=f"Failed to write file: {exc}", is_error=True)

        # Update file state cache after successful write.
        if ctx.file_state_cache is not None:
            ctx.file_state_cache.update_after_write(validated.file_path)

        num_lines = len(validated.content.splitlines())
        return ToolResult(
            output=f"Wrote {num_lines} lines to {validated.file_path}",
            side_effect=FileChanged(file_path=validated.file_path, change_type="write"),
            metadata={
                "output_type": "file_content",
                "file_path": validated.file_path,
            },
        )
