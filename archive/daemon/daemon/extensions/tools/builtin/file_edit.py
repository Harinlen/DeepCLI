"""FileEdit tool — exact string replacement in files.

Performs ``old_string → new_string`` substitution.  By default only
replaces the first occurrence and errors if ``old_string`` is not
unique (to avoid accidental edits).  Use ``replace_all=True`` for
global replacement.
"""

from __future__ import annotations

import difflib
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
from daemon.side_effects import FileChanged
from daemon.extensions.tools.builtin.file_utils import (
    check_binary,
    validate_absolute_path,
    validate_file_exists,
)

logger = logging.getLogger(__name__)


class FileEditTool(Tool):
    """Replace an exact string in a file."""

    name = "file_edit"
    description = (
        "Perform exact string replacement in a file. "
        "old_string must appear in the file. By default, it must be unique "
        "(appear exactly once); use replace_all to replace every occurrence. "
        "The file_path must be an absolute path."
    )
    permission_level = PermissionLevel.PROMPT
    concurrency = ConcurrencyHint.KEYED

    class Input(BaseModel):
        """Parameters for the file_edit tool."""

        file_path: str = Field(description="Absolute path to the file to edit.")
        old_string: str = Field(min_length=1, description="The exact text to find and replace.")
        new_string: str = Field(description="The replacement text.")
        replace_all: bool = Field(
            default=False,
            description="Replace all occurrences instead of requiring uniqueness.",
        )

    def concurrency_key(self, params: dict[str, Any]) -> str | None:
        """Serialize edits to the same file."""
        return params.get("file_path")

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Replace old_string with new_string in the target file.

        Args:
            params: Must contain ``file_path``, ``old_string``, ``new_string``.
            ctx: Execution context (unused for edits).

        Returns:
            ToolResult confirming the edit, or an error if the string
            is not found / not unique.
        """
        validated = self.Input.model_validate(params)
        path = Path(validated.file_path)

        if err := validate_absolute_path(validated.file_path):
            return err
        if err := validate_file_exists(validated.file_path):
            return err
        if err := check_binary(path):
            return err

        # Stale-write check: reject if file modified since last read.
        if ctx.file_state_cache is not None:
            ok, msg = ctx.file_state_cache.check_before_write(validated.file_path)
            if not ok:
                return ToolResult(output=msg, is_error=True)

        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return ToolResult(output=f"Cannot read file: {exc}", is_error=True)

        count = content.count(validated.old_string)

        if count == 0:
            return ToolResult(
                output="old_string not found in file.",
                is_error=True,
            )

        if not validated.replace_all and count > 1:
            return ToolResult(
                output=(
                    f"old_string appears {count} times — not unique. "
                    "Provide more context or set replace_all=true."
                ),
                is_error=True,
            )

        if validated.old_string == validated.new_string:
            return ToolResult(
                output="old_string and new_string are identical, nothing to do.",
                is_error=True,
            )

        # Perform replacement
        if validated.replace_all:
            new_content = content.replace(validated.old_string, validated.new_string)
        else:
            new_content = content.replace(validated.old_string, validated.new_string, 1)

        try:
            path.write_text(new_content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(output=f"Failed to write file: {exc}", is_error=True)

        # Update file state cache after successful write.
        if ctx.file_state_cache is not None:
            ctx.file_state_cache.update_after_write(validated.file_path)

        replaced = count if validated.replace_all else 1

        # Generate unified diff for CLI rendering.
        diff_lines = difflib.unified_diff(
            content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a{validated.file_path}",
            tofile=f"b{validated.file_path}",
            n=3,
        )
        diff_text = "".join(diff_lines)
        # Output sent to LLM is concise; diff is for CLI rendering.
        output = f"Replaced {replaced} occurrence(s) in {validated.file_path}"
        if diff_text:
            output += f"\n{diff_text}"

        return ToolResult(
            output=output,
            side_effect=FileChanged(file_path=validated.file_path, change_type="edit"),
            metadata={
                "output_type": "diff",
                "file_path": validated.file_path,
            },
        )
