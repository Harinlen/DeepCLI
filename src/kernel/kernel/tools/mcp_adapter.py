"""MCPAdapter — wrap a single MCP tool as a kernel Tool.

Lives in the Tools subsystem (not in ``kernel.mcp``) because it
implements the ``Tool`` ABC and registers into ``ToolRegistry``.
MCPManager is a dependency, not the owner.

Mirrors Claude Code ``tools/MCPTool/MCPTool.ts``.

Design doc: ``docs/plans/landed/mcp-manager.md`` § 8
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, ClassVar

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.tool import Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallResult,
)

if TYPE_CHECKING:
    from kernel.mcp import MCPManager
    from kernel.mcp.types import McpToolDef
    from kernel.tools.context import ToolContext

# CC caps MCP tool descriptions at 2 048 chars.
_MAX_DESCRIPTION_CHARS: int = 2048


class MCPAdapter(Tool[dict[str, Any], dict[str, Any]]):
    """Wraps one MCP server tool as a kernel Tool instance.

    Each adapter is a singleton within ``ToolRegistry`` (keyed by
    the normalised ``mcp__<server>__<tool>`` name).

    Attributes:
        name: ``mcp__<server>__<tool>`` — globally unique.
        description: Truncated server-provided description.
        input_schema: JSON Schema from the MCP ``tools/list`` response.
    """

    # Class-level defaults for MCP tools.
    kind: ClassVar[ToolKind] = ToolKind.other
    # TODO: flip to True once ToolSearchTool is implemented.
    should_defer: ClassVar[bool] = False
    always_load: ClassVar[bool] = False
    cache: ClassVar[bool] = True
    interrupt_behavior: ClassVar = "block"

    def __init__(
        self,
        server_name: str,
        tool_def: McpToolDef,
        mcp_manager: MCPManager,
    ) -> None:
        self._server_name = server_name
        self._tool_def = tool_def
        self._mcp = mcp_manager
        self._original_tool_name = tool_def.name

        # Override instance-level attributes from the tool def.
        self.name = build_mcp_tool_name(server_name, tool_def.name)  # type: ignore[misc]
        self.description = tool_def.description[:_MAX_DESCRIPTION_CHARS]  # type: ignore[misc]
        self.input_schema = tool_def.input_schema  # type: ignore[misc]

    # ── Tool interface ──────────────────────────────────────────────

    async def call(  # type: ignore[override]
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallResult, None]:
        """Delegate to ``MCPManager.call_tool()``."""
        result = await self._mcp.call_tool(
            self._server_name,
            self._original_tool_name,
            input,
        )
        text = extract_text_content(result.content)
        yield ToolCallResult(
            data=result.content,
            llm_content=[TextBlock(text=text)],
            display=TextDisplay(text=text),
        )

    def user_facing_name(self, _input: dict[str, Any]) -> str:
        """Show the original tool name (without mcp__ prefix)."""
        return f"{self._server_name}/{self._original_tool_name}"

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        """MCP tools always prompt — external code, unknown risk."""
        return PermissionSuggestion(
            risk="medium",
            default_decision="ask",
            reason="MCP tool (external server)",
        )


# ── Naming utility ──────────────────────────────────────────────────

_NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9_-]")


def _normalize(s: str) -> str:
    """Keep ``[a-zA-Z0-9_-]``, replace everything else with ``_``."""
    return _NON_ALNUM_RE.sub("_", s)


def build_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Build the canonical ``mcp__<server>__<tool>`` name.

    Mirrors CC ``mcpStringUtils.ts`` ``buildMcpToolName()``.
    """
    return f"mcp__{_normalize(server_name)}__{_normalize(tool_name)}"


# ── Content extraction ──────────────────────────────────────────────


def extract_text_content(content_blocks: list[dict[str, Any]]) -> str:
    """Concatenate text blocks from an MCP tool result.

    Non-text blocks (images, resources) get a placeholder line.
    Mirrors CC ``MCPTool`` content extraction.
    """
    parts: list[str] = []
    for block in content_blocks:
        block_type = block.get("type", "")
        if block_type == "text":
            parts.append(block.get("text", ""))
        elif block_type == "image":
            parts.append("[image]")
        elif block_type == "resource":
            uri = block.get("resource", {}).get("uri", "")
            parts.append(f"[resource: {uri}]")
        else:
            parts.append(f"[{block_type}]")
    return "\n".join(parts)
