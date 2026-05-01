"""Glob — path-pattern file search."""

from __future__ import annotations

import logging
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


_MAX_RESULTS = 500


class GlobTool(Tool[dict[str, Any], list[str]]):
    """Find files matching a glob pattern."""

    name = "Glob"
    description_key = "tools/glob"
    description = "Find files by glob pattern."
    kind = ToolKind.search

    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {
                "type": "string",
                "description": "Base directory; defaults to cwd.",
            },
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

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        pattern = input["pattern"]
        base_str = input.get("path")
        base = Path(base_str) if base_str else ctx.cwd
        if not base.is_absolute():
            base = ctx.cwd / base

        matches: list[Path] = []
        try:
            for match in base.glob(pattern):
                matches.append(match)
                if len(matches) >= _MAX_RESULTS:
                    break
        except OSError as exc:
            err = f"glob failed: {exc}"
            yield ToolCallResult(
                data={"error": err},
                llm_content=[TextBlock(type="text", text=err)],
                display=LocationsDisplay(locations=[], summary=err),
            )
            return

        # Sort newest-first so recently-touched files bubble up.
        matches.sort(key=lambda p: p.stat().st_mtime_ns if p.exists() else 0, reverse=True)

        locations = [{"path": str(p)} for p in matches]
        body_lines = [str(p) for p in matches]
        summary = f"{len(matches)} matches for {pattern!r}"
        if len(matches) == _MAX_RESULTS:
            summary += f" (truncated at {_MAX_RESULTS})"
        body = summary + "\n" + "\n".join(body_lines) if body_lines else summary

        yield ToolCallResult(
            data={"pattern": pattern, "matches": [str(p) for p in matches]},
            llm_content=[TextBlock(type="text", text=body)],
            display=LocationsDisplay(locations=locations, summary=summary),
        )


__all__ = ["GlobTool"]
