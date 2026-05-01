"""FileEdit — exact string replace in a file, gated by FileStateCache."""

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


class FileEditTool(Tool[dict[str, Any], str]):
    """Replace the first (or every) occurrence of a string in a file."""

    name = "FileEdit"
    description_key = "tools/file_edit"
    description = "Perform exact string replacements in files."
    kind = ToolKind.edit

    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        path_str = str(input.get("path", ""))
        try:
            resolved = Path(path_str).resolve() if path_str else None
        except OSError:
            resolved = None
        cwd = ctx.cwd.resolve()

        if resolved is not None and _is_within(resolved, cwd):
            return PermissionSuggestion(
                risk="low",
                default_decision="allow",
                reason="edit within cwd",
            )
        return PermissionSuggestion(
            risk="high",
            default_decision="ask",
            reason="editing path outside cwd",
        )

    def prepare_permission_matcher(self, input: dict[str, Any]):  # noqa: ANN201
        path = str(input.get("path", ""))
        return lambda pattern: fnmatch(path, pattern)

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        # Edits are nominally reversible (git history), but we treat
        # them as non-destructive for the allow_always decision.  True
        # destructive operations are FileWrite + irreversible Bash.
        return False

    async def validate_input(self, input: dict[str, Any], ctx: RiskContext) -> None:
        path = input.get("path")
        if not isinstance(path, str) or not path:
            raise ToolInputError("path must be a non-empty string")
        old = input.get("old_string")
        if not isinstance(old, str):
            raise ToolInputError("old_string must be a string")
        new = input.get("new_string")
        if not isinstance(new, str):
            raise ToolInputError("new_string must be a string")
        if old == new:
            raise ToolInputError("old_string and new_string are identical")

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        path = _resolve(Path(input["path"]), ctx.cwd)
        old = input["old_string"]
        new = input["new_string"]
        replace_all = bool(input.get("replace_all", False))

        if not path.exists():
            err = f"file not found: {path}"
            yield _error_result(path, err)
            return

        current = path.read_text(encoding="utf-8")

        recorded = ctx.file_state.verify(path)
        if recorded is not None and recorded.sha256_hex != hash_text(current):
            err = f"file {path} changed on disk since last read — re-read before editing"
            yield _error_result(path, err)
            return

        if old not in current:
            err = "old_string not found in file"
            yield _error_result(path, err)
            return

        if not replace_all and current.count(old) > 1:
            err = "old_string appears multiple times; pass replace_all=true or narrow the match"
            yield _error_result(path, err)
            return

        updated = current.replace(old, new) if replace_all else current.replace(old, new, 1)

        path.write_text(updated, encoding="utf-8")
        ctx.file_state.invalidate(path)

        body = f"edited {path}"
        yield ToolCallResult(
            data={"path": str(path), "replaced": True},
            llm_content=[TextBlock(type="text", text=body)],
            display=DiffDisplay(path=str(path), before=current, after=updated),
        )


def _resolve(path: Path, cwd: Path) -> Path:
    return path if path.is_absolute() else (cwd / path)


def _is_within(path: Path, cwd: Path) -> bool:
    try:
        path.relative_to(cwd)
    except ValueError:
        return False
    return True


def _error_result(path: Path, err: str) -> ToolCallResult:
    return ToolCallResult(
        data={"path": str(path), "error": err},
        llm_content=[TextBlock(type="text", text=err)],
        display=DiffDisplay(path=str(path), before=None, after=err),
    )


__all__ = ["FileEditTool"]
