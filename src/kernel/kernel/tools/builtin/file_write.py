"""FileWrite — create or overwrite a file with new content."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.context import ToolContext
from kernel.tools.file_state import hash_text
from kernel.tools.tool import RiskContext, Tool
from kernel.tools.types import (
    DiffDisplay,
    PermissionSuggestion,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)

logger = logging.getLogger(__name__)


class FileWriteTool(Tool[dict[str, Any], str]):
    """Create a new file or overwrite an existing one."""

    name = "FileWrite"
    description_key = "tools/file_write"
    description = "Write a file to the local filesystem."
    kind = ToolKind.edit

    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        path_str = str(input.get("path", ""))
        try:
            resolved = Path(path_str).resolve() if path_str else None
        except OSError:
            resolved = None
        cwd = ctx.cwd.resolve()

        if resolved is None or not _is_within(resolved, cwd):
            return PermissionSuggestion(
                risk="high",
                default_decision="ask",
                reason="write outside cwd",
            )
        if resolved.exists():
            return PermissionSuggestion(
                risk="medium",
                default_decision="ask",
                reason="overwriting existing file",
            )
        return PermissionSuggestion(
            risk="low",
            default_decision="allow",
            reason="creating new file in cwd",
        )

    def prepare_permission_matcher(self, input: dict[str, Any]):  # noqa: ANN201
        path = str(input.get("path", ""))
        return lambda pattern: fnmatch(path, pattern)

    def is_destructive(self, input: dict[str, Any]) -> bool:
        """True when overwriting an existing file."""
        path_str = str(input.get("path", ""))
        if not path_str:
            return False
        try:
            return Path(path_str).exists()
        except OSError:
            return False

    async def validate_input(self, input: dict[str, Any], ctx: RiskContext) -> None:
        path = input.get("path")
        if not isinstance(path, str) or not path:
            raise ToolInputError("path must be a non-empty string")
        content = input.get("content")
        if not isinstance(content, str):
            raise ToolInputError("content must be a string")

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        path = _resolve(Path(input["path"]), ctx.cwd)
        content = input["content"]

        existing: str | None = None
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            recorded = ctx.file_state.verify(path)
            if recorded is not None and recorded.sha256_hex != hash_text(existing):
                err = f"file {path} changed on disk since last read — re-read before overwriting"
                yield ToolCallResult(
                    data={"path": str(path), "error": err},
                    llm_content=[TextBlock(type="text", text=err)],
                    display=DiffDisplay(path=str(path), before=existing, after=err),
                )
                return

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        ctx.file_state.invalidate(path)

        action = "wrote" if existing is None else "overwrote"
        body = f"{action} {path}"
        yield ToolCallResult(
            data={"path": str(path), "action": action},
            llm_content=[TextBlock(type="text", text=body)],
            display=DiffDisplay(path=str(path), before=existing, after=content),
        )


def _resolve(path: Path, cwd: Path) -> Path:
    return path if path.is_absolute() else (cwd / path)


def _is_within(path: Path, cwd: Path) -> bool:
    try:
        path.relative_to(cwd)
    except ValueError:
        return False
    return True


__all__ = ["FileWriteTool"]
