"""MCP tool bridge — convert MCP tools into Mustang Tool instances.

For each tool advertised by an MCP server, the bridge creates a
``McpProxyTool`` that wraps the tool's schema and delegates execution
to ``McpClient.call_tool()``.  The proxy tools are then registered in
the shared ``ToolRegistry`` so the orchestrator and LLM see them as
normal tools.

Tool naming follows Claude Code's convention:

    mcp__{normalized_server}__{normalized_tool}

Non-alphanumeric characters (except ``_`` and ``-``) are replaced
with ``_``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel

from daemon.extensions.mcp.client import McpClient
from daemon.extensions.mcp.resource_cache import ResourceCache
from daemon.extensions.tools.base import (
    ConcurrencyHint,
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.extensions.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Regex: keep alphanumeric, underscore, hyphen — replace rest with _
_NORMALIZE_RE = re.compile(r"[^a-zA-Z0-9_-]")

# Max description length sent to LLM (avoids wasting context)
_MAX_DESCRIPTION_LEN = 1024


def normalize_mcp_name(name: str) -> str:
    """Normalize a name for use in an MCP tool identifier.

    Replaces characters outside ``[a-zA-Z0-9_-]`` with ``_``.
    """
    return _NORMALIZE_RE.sub("_", name)


def build_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Build the canonical ``mcp__<server>__<tool>`` name."""
    return f"mcp__{normalize_mcp_name(server_name)}__{normalize_mcp_name(tool_name)}"


class McpProxyTool(Tool):
    """Proxy that delegates execution to an MCP server tool.

    Created dynamically by :class:`McpBridge` for each tool returned
    by ``tools/list``.  The class-level attributes satisfy
    ``Tool.__init_subclass__`` validation; instance-level attributes
    are set in ``__init__`` to the actual MCP tool values.

    MCP tools default to ``PermissionLevel.PROMPT`` because they
    execute external code from user-configured servers.
    """

    # Class-level defaults satisfy __init_subclass__ validation.
    # Overridden per-instance in __init__.
    name = "_mcp_proxy"
    description = "MCP proxy tool"
    permission_level = PermissionLevel.PROMPT

    class Input(BaseModel):
        """Placeholder — actual schema is set per-instance."""

    def __init__(
        self,
        client: McpClient,
        server_name: str,
        tool_def: dict[str, Any],
    ) -> None:
        self._client = client
        self._server_name = server_name
        self._original_name = tool_def["name"]

        # Override class-level attributes with actual values
        self.name = build_mcp_tool_name(server_name, self._original_name)
        desc = tool_def.get("description", f"MCP tool: {self._original_name}")
        self.description = desc[:_MAX_DESCRIPTION_LEN]

        # Store raw input schema for input_schema() override
        self._input_schema_raw: dict[str, Any] = tool_def.get("inputSchema", {})

    @classmethod
    def input_schema(cls) -> dict[str, Any]:
        """Overridden at instance level — see ``_input_schema_instance``."""
        # This classmethod is only called if someone calls it on the
        # class itself rather than an instance.  Instances override via
        # the bound method below.
        return {}

    def _input_schema_instance(self) -> dict[str, Any]:
        """Return the MCP tool's input schema."""
        schema = dict(self._input_schema_raw)
        schema.pop("title", None)
        return schema

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Skip validation for this dynamic proxy class."""
        super().__init_subclass__(**kwargs)

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Forward the call to the MCP server.

        Concatenates text content blocks from the response.  Large
        outputs are persisted via the result store and only a summary
        is returned.

        Args:
            params: Tool arguments from the LLM.
            ctx: Execution context (unused by MCP tools).

        Returns:
            ToolResult with combined text output or error message.
        """
        try:
            result = await self._client.call_tool(self._original_name, params)
        except Exception as exc:
            return ToolResult(output=f"MCP tool error: {exc}", is_error=True)

        is_error = result.get("isError", False)
        output = _extract_text_content(result)

        # Budget enforcement is handled by the orchestrator's
        # ResultStore.apply_budget() — no need to pre-check here.
        return ToolResult(output=output, is_error=is_error)


def _extract_text_content(result: dict[str, Any]) -> str:
    """Concatenate text blocks from an MCP tool result.

    MCP results have a ``content`` array with typed blocks.  Text
    blocks are concatenated; other types get a placeholder.
    """
    content = result.get("content", [])
    if not content:
        return result.get("text", "(empty result)")

    parts: list[str] = []
    for block in content:
        block_type = block.get("type", "text")
        if block_type == "text":
            parts.append(block.get("text", ""))
        elif block_type == "image":
            mime = block.get("mimeType", "unknown")
            parts.append(f"[Image: {mime}]")
        elif block_type == "resource":
            uri = block.get("resource", {}).get("uri", "unknown")
            parts.append(f"[Resource: {uri}]")
        else:
            parts.append(f"[{block_type}]")

    return "\n".join(parts)


class McpResourceListTool(Tool):
    """Proxy tool for listing an MCP server's resources.

    Registered per-server as ``mcp__{server}__list_resources``.
    Only created when the server advertises resource capabilities.
    """

    name = "_mcp_list_resources"
    description = "List resources from MCP server"
    permission_level = PermissionLevel.PROMPT
    concurrency = ConcurrencyHint.PARALLEL

    class Input(BaseModel):
        """No parameters — lists all available resources."""

    def __init__(self, client: McpClient, server_name: str) -> None:
        self._client = client
        self.name = f"mcp__{normalize_mcp_name(server_name)}__list_resources"
        self.description = f"List available resources from MCP server '{server_name}'"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            resources = await self._client.list_resources()
            import json as _json

            return ToolResult(output=_json.dumps(resources, indent=2))
        except Exception as exc:
            return ToolResult(output=f"Failed to list resources: {exc}", is_error=True)


class McpResourceReadTool(Tool):
    """Proxy tool for reading an MCP server resource by URI.

    Registered per-server as ``mcp__{server}__read_resource``.
    Uses a per-bridge :class:`ResourceCache` for LRU+TTL caching.
    """

    name = "_mcp_read_resource"
    description = "Read a resource from MCP server"
    permission_level = PermissionLevel.PROMPT
    concurrency = ConcurrencyHint.PARALLEL

    class Input(BaseModel):
        """Input schema for read_resource."""

        uri: str

    def __init__(
        self,
        client: McpClient,
        server_name: str,
        cache: ResourceCache,
    ) -> None:
        self._client = client
        self._cache = cache
        self.name = f"mcp__{normalize_mcp_name(server_name)}__read_resource"
        self.description = f"Read a resource by URI from MCP server '{server_name}'"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        uri = params.get("uri", "")
        if not uri:
            return ToolResult(output="Missing required 'uri' parameter", is_error=True)

        # Check cache first
        cached = self._cache.get(uri)
        if cached is not None:
            return ToolResult(output=cached.content)

        try:
            result = await self._client.read_resource(uri)
            content = _extract_resource_content(result)

            # Cache the result
            self._cache.put(uri, content)

            return ToolResult(output=content)
        except Exception as exc:
            return ToolResult(
                output=f"Failed to read resource '{uri}': {exc}",
                is_error=True,
            )


def _extract_resource_content(result: dict[str, Any]) -> str:
    """Extract text content from a resources/read response.

    MCP resource responses contain a ``contents`` array.  Text
    content is concatenated directly; blob content is kept as-is
    (base64).
    """
    contents = result.get("contents", [])
    if not contents:
        return "(empty resource)"

    parts: list[str] = []
    for entry in contents:
        if "text" in entry:
            parts.append(entry["text"])
        elif "blob" in entry:
            mime = entry.get("mimeType", "application/octet-stream")
            parts.append(f"[Binary resource: {mime}, base64-encoded]")
            parts.append(entry["blob"])
        else:
            parts.append(f"[Resource: {entry.get('uri', 'unknown')}]")

    return "\n".join(parts)


class McpBridge:
    """Bridges an MCP server's tools into the Mustang tool registry.

    Manages the set of proxy tools for one MCP server.  On reconnect,
    ``sync_tools()`` can be called again to refresh the tool list.

    Also registers resource proxy tools (``list_resources`` /
    ``read_resource``) when the server advertises resource
    capabilities.

    Args:
        client: Connected MCP client.
        tool_registry: Shared tool registry to add/remove proxy tools.
        tools_concurrency: Per-tool concurrency overrides.
        resource_ttl: TTL for cached resources (seconds).
    """

    def __init__(
        self,
        client: McpClient,
        tool_registry: ToolRegistry,
        tools_concurrency: dict[str, str] | None = None,
        resource_ttl: float = 300.0,
    ) -> None:
        self._client = client
        self._tool_registry = tool_registry
        self._tools_concurrency = tools_concurrency or {}
        self._resource_cache = ResourceCache(default_ttl=resource_ttl)
        self._registered_names: list[str] = []

    async def sync_tools(self) -> list[str]:
        """Fetch tools from the MCP server and register proxy tools.

        Also registers resource proxy tools if the server advertises
        resource capabilities.  Removes previously registered tools
        first (for reconnect scenarios).

        Returns:
            List of registered tool names (``mcp__server__tool``).
        """
        # Remove old tools from a previous sync
        self._unregister_tools()

        tool_defs = await self._client.list_tools()
        registered: list[str] = []

        for tool_def in tool_defs:
            if "name" not in tool_def:
                logger.warning("MCP tool missing 'name' — skipping")
                continue

            proxy = McpProxyTool(
                client=self._client,
                server_name=self._client.server_name,
                tool_def=tool_def,
            )

            # Apply concurrency override from mcp.json tools_concurrency
            original_name = tool_def["name"]
            hint_str = self._tools_concurrency.get(original_name)
            if hint_str:
                try:
                    proxy.concurrency = ConcurrencyHint(hint_str)
                except ValueError:
                    logger.warning(
                        "Invalid concurrency hint '%s' for MCP tool '%s' — keeping serial",
                        hint_str,
                        original_name,
                    )

            # Bind instance-level input_schema
            proxy.input_schema = proxy._input_schema_instance  # type: ignore[assignment]

            if proxy.name in self._tool_registry:
                logger.warning(
                    "MCP tool '%s' conflicts with existing tool — skipping",
                    proxy.name,
                )
                continue

            self._tool_registry.register(proxy)
            registered.append(proxy.name)

        # Register resource tools if server supports resources
        registered.extend(self._register_resource_tools())

        self._registered_names = registered
        if registered:
            logger.info(
                "MCP server '%s' — registered %d tools: %s",
                self._client.server_name,
                len(registered),
                ", ".join(registered),
            )
        return registered

    def _register_resource_tools(self) -> list[str]:
        """Register list_resources + read_resource proxy tools.

        Only registers if the server advertises resource capabilities.
        Returns the list of registered tool names.
        """
        caps = self._client.server_capabilities
        if "resources" not in caps:
            return []

        server_name = self._client.server_name
        registered: list[str] = []

        # list_resources
        list_tool = McpResourceListTool(self._client, server_name)
        if list_tool.name not in self._tool_registry:
            self._tool_registry.register(list_tool)
            registered.append(list_tool.name)

        # read_resource
        read_tool = McpResourceReadTool(self._client, server_name, self._resource_cache)
        if read_tool.name not in self._tool_registry:
            self._tool_registry.register(read_tool)
            registered.append(read_tool.name)

        return registered

    def get_tool_names(self) -> list[str]:
        """Return currently registered MCP tool names."""
        return list(self._registered_names)

    def _unregister_tools(self) -> None:
        """Remove previously registered proxy tools from the registry."""
        for name in self._registered_names:
            self._tool_registry.unregister(name)
        self._registered_names = []
