"""Map tool runtime results into Orchestrator events and LLM content."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from kernel.llm.types import TextContent, ToolResultContent, ToolUseContent
from kernel.orchestrator.events import ToolCallError, ToolCallStart
from kernel.orchestrator.tool_kinds import ToolKind
from kernel.orchestrator.tools_exec.shared import EventPair


class ToolResultMappingMixin:
    """Helpers that map errors and unknown tools to event/result pairs."""

    async def _error_unknown_tool(self, tc: ToolUseContent) -> AsyncGenerator[EventPair, None]:
        """Emit a start event and matching error result for an unknown tool.

        Args:
            tc: Tool-use block whose tool name was not registered.

        Yields:
            Tool start event followed by a tool error/result pair.
        """
        yield (ToolCallStart(id=tc.id, title=tc.name, kind=ToolKind.other), None)
        yield self._error_tuple(tc, f"tool {tc.name!r} is not registered")

    def _error_tuple(
        self, tc: ToolUseContent, message: str
    ) -> tuple[ToolCallError, ToolResultContent]:
        """Create matching event and LLM result for a tool failure.

        Args:
            tc: Tool-use block that failed.
            message: Error message for both client and model.

        Returns:
            Tool error event and matching error ``ToolResultContent``.
        """
        return (
            ToolCallError(id=tc.id, error=message),
            ToolResultContent(tool_use_id=tc.id, content=message, is_error=True),
        )


def coerce_content(blocks: list[Any]) -> str | list[Any]:
    """Pack ``list[ContentBlock]`` into a shape the LLM layer accepts.

    Args:
        blocks: Tool result content blocks.

    Returns:
        Joined text for text-only output, otherwise a copied block list.
    """
    if all(isinstance(b, TextContent) or hasattr(b, "text") for b in blocks):
        return "\n".join(getattr(b, "text", "") for b in blocks)
    return list(blocks)


def apply_result_budget(content: str | list[Any], budget: int) -> str | list[Any]:
    """Truncate an oversized string tool result.

    Args:
        content: LLM-facing tool result content.
        budget: Maximum allowed characters for string output.

    Returns:
        Original content when within budget, otherwise a truncated string.
    """
    if not isinstance(content, str) or len(content) <= budget:
        return content
    original_size = len(content)
    return (
        content[:budget] + f"\n\n[tool result truncated — {original_size} chars, "
        f"kept first {budget} chars]"
    )
