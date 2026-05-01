"""REPL — batch-execution wrapper for primitive tools.

When enabled via ``ToolFlags.repl``, primitive tools (Bash, FileRead,
FileEdit, FileWrite, Glob, Grep, Agent, etc.) are hidden from the LLM's
direct tool list.  The LLM calls REPL instead, submitting an array of
tool invocations that are dispatched internally and returned as a single
combined result.

Purpose: reduce round-trips between LLM and tool execution by batching
multiple operations into one tool call.

Design mirrors Claude Code's ``REPLTool`` (``tools/REPLTool/``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.context import ToolContext
from kernel.tools.tool import RiskContext, Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)

if TYPE_CHECKING:
    from kernel.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Tools hidden from the LLM when REPL mode is active.
# They remain in the registry lookup table for internal dispatch.
REPL_HIDDEN_TOOLS: frozenset[str] = frozenset(
    {
        "Bash",
        "Cmd",
        "PowerShell",
        "Python",
        "FileRead",
        "FileEdit",
        "FileWrite",
        "Glob",
        "Grep",
        "Agent",
    }
)

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Optional correlation id for this call.",
                    },
                    "tool_name": {
                        "type": "string",
                        "description": "Name of the tool to invoke.",
                    },
                    "input": {
                        "type": "object",
                        "description": "Input parameters for the tool.",
                    },
                },
                "required": ["tool_name", "input"],
            },
            "description": "Array of tool calls to execute in batch.",
        },
    },
    "required": ["calls"],
}


class ReplTool(Tool[dict[str, Any], list[dict[str, Any]]]):
    """Batch-execute hidden primitive tools in a single call."""

    name = "REPL"
    description_key = "tools/repl"
    description = "Execute one or more tool calls in a single batch."
    kind = ToolKind.execute
    should_defer = False
    always_load = True
    cache = True
    max_result_size_chars = 200_000
    interrupt_behavior = "cancel"

    input_schema = _INPUT_SCHEMA

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    # ------------------------------------------------------------------
    # Tool contract
    # ------------------------------------------------------------------

    def activity_description(self, input: dict[str, Any]) -> str | None:
        calls = input.get("calls", [])
        if not calls:
            return "Executing REPL (empty batch)"
        parts: list[str] = []
        for c in calls[:5]:
            name = c.get("tool_name", "?")
            detail = self._call_detail(name, c.get("input", {}))
            parts.append(f"{name}: {detail}" if detail else name)
        label = ", ".join(parts)
        if len(calls) > 5:
            label += f", ... ({len(calls)} total)"
        return f"Batch executing {len(calls)} tools: {label}"

    @staticmethod
    def _call_detail(tool_name: str, tool_input: dict[str, Any]) -> str | None:
        """Extract a short human-readable detail from an inner tool call."""
        if tool_name == "Bash":
            cmd = tool_input.get("command", "")
            if len(cmd) > 120:
                cmd = cmd[:117] + "..."
            return cmd or None
        if tool_name in ("Read", "FileRead"):
            return tool_input.get("file_path")
        if tool_name in ("Edit", "FileEdit"):
            return tool_input.get("file_path")
        if tool_name in ("Write", "FileWrite"):
            return tool_input.get("file_path")
        if tool_name == "Glob":
            return tool_input.get("pattern")
        if tool_name == "Grep":
            return tool_input.get("pattern")
        return None

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        # Check each inner tool.  Read-only tools are always fine.
        # For mutating tools, delegate to the tool's own default_risk —
        # this lets BashTool's compound-command classifier auto-allow
        # safe commands like ``echo hello | cat`` instead of blindly
        # blocking everything with ``kind=execute``.
        calls = input.get("calls", [])
        worst: PermissionSuggestion | None = None
        for call in calls:
            tool_name = call.get("tool_name", "")
            tool = self._registry.lookup(tool_name)
            if tool is None:
                continue
            if tool.is_read_only:
                continue
            # Delegate to the tool's own risk judgment.
            inner = tool.default_risk(call.get("input", {}), ctx)
            if inner.default_decision == "deny":
                return inner  # deny short-circuits
            if inner.default_decision == "ask":
                worst = inner  # remember the worst non-deny
        if worst is not None:
            return worst
        return PermissionSuggestion(
            risk="low",
            default_decision="allow",
            reason="batch contains only safe tools",
        )

    def is_destructive(self, input: dict[str, Any]) -> bool:
        calls = input.get("calls", [])
        for call in calls:
            tool_name = call.get("tool_name", "")
            tool = self._registry.lookup(tool_name)
            if tool is not None and tool.is_destructive(call.get("input", {})):
                return True
        return False

    async def validate_input(self, input: dict[str, Any], ctx: RiskContext) -> None:
        calls = input.get("calls")
        if not isinstance(calls, list):
            raise ToolInputError("calls must be an array")
        if not calls:
            raise ToolInputError("calls must not be empty")
        for i, call in enumerate(calls):
            if not isinstance(call, dict):
                raise ToolInputError(f"calls[{i}] must be an object")
            tool_name = call.get("tool_name")
            if not isinstance(tool_name, str) or not tool_name:
                raise ToolInputError(f"calls[{i}].tool_name must be a non-empty string")
            if tool_name not in REPL_HIDDEN_TOOLS:
                raise ToolInputError(
                    f"calls[{i}].tool_name {tool_name!r} is not a REPL-managed tool. "
                    f"Call it directly instead."
                )
            tool = self._registry.lookup(tool_name)
            if tool is None:
                raise ToolInputError(f"calls[{i}].tool_name {tool_name!r} not found in registry")
            tool_input = call.get("input")
            if not isinstance(tool_input, dict):
                raise ToolInputError(f"calls[{i}].input must be an object")

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        calls: list[dict[str, Any]] = input["calls"]
        results: list[dict[str, Any]] = []
        result_texts: list[str] = []

        # Partition into batches: consecutive read-only tools run
        # concurrently, non-safe tools run alone serially.
        batches = _partition_calls(calls, self._registry)

        for batch in batches:
            if len(batch) == 1:
                # Single call — run directly.
                idx, call = batch[0]
                text, data = await self._run_one(idx, call, ctx)
                result_texts.append(text)
                results.append(data)
            else:
                # Concurrent batch of read-only tools.
                coros = [self._run_one(idx, call, ctx) for idx, call in batch]
                batch_results = await asyncio.gather(*coros, return_exceptions=True)
                for (idx, call), result in zip(batch, batch_results):
                    if isinstance(result, BaseException):
                        call_id = call.get("id", "")
                        tool_name = call.get("tool_name", "?")
                        id_attr = f' id="{call_id}"' if call_id else ""
                        err_text = (
                            f'<repl_result index="{idx}" tool="{tool_name}"'
                            f'{id_attr} error="true">\n'
                            f"{result!s}\n"
                            f"</repl_result>"
                        )
                        result_texts.append(err_text)
                        results.append({"index": idx, "tool": tool_name, "error": str(result)})
                    else:
                        text, data = result
                        result_texts.append(text)
                        results.append(data)

        combined = "\n".join(result_texts)
        yield ToolCallResult(
            data=results,
            llm_content=[TextBlock(text=combined)],
            display=TextDisplay(text=combined),
        )

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    async def _run_one(
        self,
        index: int,
        call: dict[str, Any],
        ctx: ToolContext,
    ) -> tuple[str, dict[str, Any]]:
        """Execute a single inner tool call and return (xml_text, data)."""
        tool_name: str = call["tool_name"]
        tool_input: dict[str, Any] = call["input"]
        call_id: str = call.get("id", "")
        id_attr = f' id="{call_id}"' if call_id else ""

        tool = self._registry.lookup(tool_name)
        assert tool is not None  # validated in validate_input

        try:
            await tool.validate_input(tool_input, ctx)
        except ToolInputError as exc:
            text = (
                f'<repl_result index="{index}" tool="{tool_name}"'
                f'{id_attr} error="true">\n'
                f"Input validation failed: {exc}\n"
                f"</repl_result>"
            )
            return text, {"index": index, "tool": tool_name, "error": str(exc)}

        # Collect output from the tool's async generator.
        output_parts: list[str] = []
        try:
            async for event in tool.call(tool_input, ctx):
                if isinstance(event, ToolCallResult):
                    for block in event.llm_content:
                        block_text = getattr(block, "text", None)
                        if isinstance(block_text, str):
                            output_parts.append(block_text)
                # ToolCallProgress is silently consumed; the outer REPL
                # result is the only thing the LLM sees.
        except Exception as exc:
            logger.warning("REPL: tool %s raised %s", tool_name, exc, exc_info=True)
            text = (
                f'<repl_result index="{index}" tool="{tool_name}"'
                f'{id_attr} error="true">\n'
                f"{exc!s}\n"
                f"</repl_result>"
            )
            return text, {"index": index, "tool": tool_name, "error": str(exc)}

        body = "\n".join(output_parts) if output_parts else "(no output)"
        text = f'<repl_result index="{index}" tool="{tool_name}"{id_attr}>\n{body}\n</repl_result>'
        return text, {"index": index, "tool": tool_name, "ok": True}


def _partition_calls(
    calls: list[dict[str, Any]],
    registry: ToolRegistry,
) -> list[list[tuple[int, dict[str, Any]]]]:
    """Group consecutive calls by concurrency safety.

    Returns a list of batches. Each batch is a list of
    ``(original_index, call_dict)`` pairs.  Batches of concurrency-safe
    tools can run in parallel; batches with a single unsafe tool run
    serially.
    """
    if not calls:
        return []

    batches: list[list[tuple[int, dict[str, Any]]]] = []
    current_batch: list[tuple[int, dict[str, Any]]] = []
    current_safe: bool | None = None

    for i, call in enumerate(calls):
        tool = registry.lookup(call.get("tool_name", ""))
        is_safe = tool.is_concurrency_safe if tool else False

        if current_safe is None:
            # First call.
            current_safe = is_safe
            current_batch = [(i, call)]
        elif is_safe and current_safe:
            # Extend the current safe batch.
            current_batch.append((i, call))
        else:
            # Boundary: flush current batch, start new one.
            batches.append(current_batch)
            current_batch = [(i, call)]
            current_safe = is_safe

    if current_batch:
        batches.append(current_batch)

    return batches


__all__ = ["REPL_HIDDEN_TOOLS", "ReplTool"]
