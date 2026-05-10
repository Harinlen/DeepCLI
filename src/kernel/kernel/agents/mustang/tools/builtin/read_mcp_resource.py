"""ReadMcpResourceTool — read a specific resource from an MCP server.

Mirrors Claude Code ``tools/ReadMcpResourceTool/ReadMcpResourceTool.ts``.
Deferred: loaded on demand via ToolSearchTool.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import tempfile
from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from kernel.agents.mustang.orchestrator.types import ToolKind
from kernel.core.protocol.interfaces.contracts.text_block import TextBlock
from kernel.agents.mustang.tools.context import ToolContext
from kernel.agents.mustang.tools.tool import Tool
from kernel.agents.mustang.tools.types import (
    TextDisplay,
    ToolCallResult,
    ToolInputError,
)

logger = logging.getLogger(__name__)


class ReadMcpResourceTool(Tool[dict[str, Any], dict[str, Any]]):
    """Read a specific resource from an MCP server by URI."""

    name: ClassVar[str] = "ReadMcpResource"
    description: ClassVar[str] = (
        "Read the contents of a specific resource from an MCP server. "
        "Use ListMcpResources first to discover available resource URIs. "
        "Text resources are returned inline; binary resources are saved to disk "
        "and the file path is returned."
    )
    kind: ClassVar[ToolKind] = ToolKind.read
    should_defer: ClassVar[bool] = True
    search_hint: ClassVar[str] = "read a specific MCP resource by URI"

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "The MCP server name",
            },
            "uri": {
                "type": "string",
                "description": "The resource URI to read",
            },
        },
        "required": ["server", "uri"],
    }

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallResult, None]:
        mcp = ctx.mcp_manager
        if mcp is None:
            raise ToolInputError("MCP subsystem is not enabled.")

        server_name: str | None = input.get("server")
        uri: str | None = input.get("uri")

        if not server_name:
            raise ToolInputError("'server' is required.")
        if not uri:
            raise ToolInputError("'uri' is required.")

        connected = mcp.get_connected()
        server = next((s for s in connected if s.name == server_name), None)
        if server is None:
            names = [s.name for s in connected]
            raise ToolInputError(
                f'Server "{server_name}" not found. '
                f"Available servers: {', '.join(names) or '(none)'}"
            )

        if "resources" not in server.capabilities:
            raise ToolInputError(f'Server "{server_name}" does not support resources.')

        result = await mcp.read_resource(server_name, uri)

        # Process each content entry: pass text through, persist blobs to disk.
        contents: list[dict[str, Any]] = []
        text_parts: list[str] = []

        for entry in result.contents:
            entry_uri = entry.get("uri", uri)
            mime = entry.get("mimeType")

            if "text" in entry:
                text = entry["text"]
                item: dict[str, Any] = {"uri": entry_uri, "text": text}
                if mime:
                    item["mimeType"] = mime
                contents.append(item)
                text_parts.append(text)

            elif "blob" in entry and isinstance(entry["blob"], str):
                # Decode base64 blob and save to a temp file so the LLM gets a
                # file path instead of raw base64 flooding the context window.
                # Mirrors CC's persistBinaryContent / getBinaryBlobSavedMessage.
                try:
                    raw = base64.b64decode(entry["blob"])
                    ext = _mime_to_ext(mime) if mime else ".bin"
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=ext, prefix="mcp-resource-"
                    ) as tmp:
                        tmp.write(raw)
                        saved_path = tmp.name
                    item = {
                        "uri": entry_uri,
                        "blobSavedTo": saved_path,
                        "text": f"Binary content ({len(raw)} bytes) saved to {saved_path}",
                    }
                    if mime:
                        item["mimeType"] = mime
                    contents.append(item)
                    text_parts.append(item["text"])
                except Exception as exc:
                    err_text = f"Binary content could not be saved: {exc}"
                    item = {"uri": entry_uri, "text": err_text}
                    if mime:
                        item["mimeType"] = mime
                    contents.append(item)
                    text_parts.append(err_text)

            else:
                item = {"uri": entry_uri}
                if mime:
                    item["mimeType"] = mime
                contents.append(item)

        llm_text = "\n".join(text_parts) if text_parts else f"Empty resource at {uri}"
        data = {"contents": contents}

        yield ToolCallResult(
            data=data,
            llm_content=[TextBlock(type="text", text=llm_text)],
            display=TextDisplay(text=llm_text),
        )


# ── helpers ─────────────────────────────────────────────────────────────────


def _mime_to_ext(mime_type: str) -> str:
    """Return a file extension for *mime_type*, e.g. ``"image/png"`` → ``".png"``."""
    ext = mimetypes.guess_extension(mime_type)
    if ext:
        return ext
    # Fallback for common types not always in the stdlib mapping.
    _FALLBACKS: dict[str, str] = {
        "image/webp": ".webp",
        "application/pdf": ".pdf",
        "application/json": ".json",
    }
    return _FALLBACKS.get(mime_type, ".bin")


__all__ = ["ReadMcpResourceTool"]
