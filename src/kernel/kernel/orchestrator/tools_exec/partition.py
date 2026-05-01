"""Batch partitioning for ordered tool execution."""

from __future__ import annotations

from typing import Any

from kernel.llm.types import ToolUseContent


def partition_tool_calls(
    tool_calls: list[ToolUseContent],
    lookup: Any,
) -> list[list[ToolUseContent]]:
    """Split tool calls into ordered serial/concurrent batches.

    Args:
        tool_calls: Tool uses in the order produced by the LLM.
        lookup: Tool lookup function from ToolManager, or ``None`` in degraded
            test setups.

    Returns:
        Batches that preserve global ordering while grouping adjacent tools that
        explicitly declare themselves concurrency-safe.
    """
    if not tool_calls:
        return []

    batches: list[list[ToolUseContent]] = []
    safe_acc: list[ToolUseContent] = []

    for tc in tool_calls:
        tool = lookup(tc.name) if lookup is not None else None
        is_safe = tool is not None and tool.is_concurrency_safe
        if is_safe:
            safe_acc.append(tc)
            continue
        # A non-safe tool is an ordering barrier: earlier safe reads may run
        # together, but no later tool can cross this boundary.
        if safe_acc:
            batches.append(safe_acc)
            safe_acc = []
        batches.append([tc])

    if safe_acc:
        batches.append(safe_acc)
    return batches
