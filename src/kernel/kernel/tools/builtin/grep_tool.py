"""Grep — regex search in files."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.context import ToolContext
from kernel.tools.tool import RiskContext, Tool
from kernel.tools.types import (
    LocationsDisplay,
    PermissionSuggestion,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)

logger = logging.getLogger(__name__)


_MAX_MATCHES = 500


class GrepTool(Tool[dict[str, Any], list[dict[str, Any]]]):
    """Search for a regex pattern across files."""

    name = "Grep"
    description_key = "tools/grep"
    description = "Search file contents with ripgrep."
    kind = ToolKind.search

    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {
                "type": "string",
                "description": "Directory to search; defaults to cwd.",
            },
            "glob": {
                "type": "string",
                "description": "Only search files matching this glob (e.g. '*.py').",
            },
            "case_insensitive": {"type": "boolean", "default": False},
        },
        "required": ["pattern"],
    }

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="low",
            default_decision="allow",
            reason="search is read-only",
        )

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    async def validate_input(self, input: dict[str, Any], ctx: RiskContext) -> None:
        pattern = input.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ToolInputError("pattern must be a non-empty string")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ToolInputError(f"invalid regex: {exc}")

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        pattern = input["pattern"]
        flags = re.IGNORECASE if input.get("case_insensitive") else 0
        regex = re.compile(pattern, flags)

        base_str = input.get("path")
        base = Path(base_str) if base_str else ctx.cwd
        if not base.is_absolute():
            base = ctx.cwd / base

        file_glob = input.get("glob") or "**/*"

        matches: list[dict[str, Any]] = []
        try:
            for path in base.glob(file_glob):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

                for line_num, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        matches.append(
                            {
                                "path": str(path),
                                "line": line_num,
                                "text": line.rstrip(),
                            }
                        )
                        if len(matches) >= _MAX_MATCHES:
                            break
                if len(matches) >= _MAX_MATCHES:
                    break
        except OSError as exc:
            err = f"grep failed: {exc}"
            yield ToolCallResult(
                data={"error": err},
                llm_content=[TextBlock(type="text", text=err)],
                display=LocationsDisplay(locations=[], summary=err),
            )
            return

        summary = f"{len(matches)} matches for {pattern!r}"
        if len(matches) >= _MAX_MATCHES:
            summary += f" (truncated at {_MAX_MATCHES})"

        body_lines = [f"{m['path']}:{m['line']}: {m['text']}" for m in matches]
        body = summary + "\n" + "\n".join(body_lines) if body_lines else summary

        yield ToolCallResult(
            data={"pattern": pattern, "matches": matches},
            llm_content=[TextBlock(type="text", text=body)],
            display=LocationsDisplay(
                locations=[{"path": m["path"], "line": m["line"]} for m in matches],
                summary=summary,
            ),
        )


__all__ = ["GrepTool"]
