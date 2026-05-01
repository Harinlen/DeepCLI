"""ListMcpResourcesTool — list resources from connected MCP servers.

Mirrors Claude Code ``tools/ListMcpResourcesTool/ListMcpResourcesTool.ts``.
Deferred: loaded on demand via ToolSearchTool.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.context import ToolContext
from kernel.tools.tool import Tool
from kernel.tools.types import (
    TextDisplay,
    ToolCallResult,
    ToolInputError,
)

logger = logging.getLogger(__name__)


class ListMcpResourcesTool(Tool[dict[str, Any], list[dict[str, Any]]]):
    """List all resources available from connected MCP servers."""

    name: ClassVar[str] = "ListMcpResources"
    description: ClassVar[str] = (
        "List resources exposed by connected MCP servers. "
        "Resources are data sources that MCP servers make available for reading "
        "(files, database records, API responses, etc.). "
        "Pass an optional server name to filter to one server."
    )
    kind: ClassVar[ToolKind] = ToolKind.read
    should_defer: ClassVar[bool] = True
    search_hint: ClassVar[str] = "list resources from connected MCP servers"

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "Optional server name to filter resources by",
            },
        },
    }

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallResult, None]:
        mcp = ctx.mcp_manager
        if mcp is None:
            raise ToolInputError("MCP subsystem is not enabled.")

        target_server: str | None = input.get("server")
        connected = mcp.get_connected()

        if target_server:
            servers = [s for s in connected if s.name == target_server]
            if not servers:
                names = [s.name for s in connected]
                raise ToolInputError(
                    f'Server "{target_server}" not found. '
                    f"Available servers: {', '.join(names) or '(none)'}"
                )
        else:
            servers = connected

        resources: list[dict[str, Any]] = []
        for server in servers:
            if "resources" not in server.capabilities:
                continue
            try:
                defs = await mcp.list_resources(server.name)
                for r in defs:
                    entry: dict[str, Any] = {
                        "uri": r.uri,
                        "name": r.name,
                        "server": server.name,
                    }
                    if r.mime_type is not None:
                        entry["mimeType"] = r.mime_type
                    if r.description:
                        entry["description"] = r.description
                    resources.append(entry)
            except Exception as exc:
                logger.warning("ListMcpResources[%s]: %s", server.name, exc)

        if not resources:
            text = (
                "No resources found. "
                "MCP servers may still provide tools even if they have no resources."
            )
        else:
            lines = [
                f"- [{r['server']}] {r['uri']} ({r['name']})"
                + (f" — {r['description']}" if r.get("description") else "")
                for r in resources
            ]
            text = f"{len(resources)} resource(s):\n" + "\n".join(lines)

        yield ToolCallResult(
            data=resources,
            llm_content=[TextBlock(type="text", text=text)],
            display=TextDisplay(text=text),
        )


__all__ = ["ListMcpResourcesTool"]
