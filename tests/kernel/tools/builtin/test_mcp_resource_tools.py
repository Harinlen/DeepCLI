from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kernel.mcp.types import ConnectedServer, McpResourceDef, McpResourceResult
from kernel.tools.builtin.list_mcp_resources import ListMcpResourcesTool
from kernel.tools.builtin.read_mcp_resource import ReadMcpResourceTool, _mime_to_ext
from kernel.tools.context import ToolContext
from kernel.tools.types import ToolInputError


def _ctx(mcp_manager: Any = None) -> ToolContext:
    return ToolContext(
        session_id="s-1",
        agent_depth=0,
        agent_id=None,
        cwd=Path("/tmp"),
        cancel_event=asyncio.Event(),
        file_state=MagicMock(),
        mcp_manager=mcp_manager,
    )


async def _collect(tool: Any, input: dict[str, Any], ctx: ToolContext) -> list[Any]:
    return [event async for event in tool.call(input, ctx)]


def _server(name: str, capabilities: dict[str, Any] | None = None) -> ConnectedServer:
    return ConnectedServer(
        name=name,
        client=MagicMock(),
        capabilities=capabilities or {"resources": {}},
    )


@pytest.mark.anyio
async def test_list_mcp_resources_requires_mcp_subsystem() -> None:
    with pytest.raises(ToolInputError, match="MCP subsystem is not enabled"):
        await _collect(ListMcpResourcesTool(), {}, _ctx(None))


@pytest.mark.anyio
async def test_list_mcp_resources_filters_server_and_returns_structured_entries() -> None:
    mcp = MagicMock()
    mcp.get_connected.return_value = [_server("alpha"), _server("beta")]
    mcp.list_resources = AsyncMock(
        return_value=[
            McpResourceDef(
                uri="file:///notes.md",
                name="notes",
                description="project notes",
                mime_type="text/markdown",
            )
        ]
    )

    [result] = await _collect(ListMcpResourcesTool(), {"server": "alpha"}, _ctx(mcp))

    mcp.list_resources.assert_awaited_once_with("alpha")
    assert result.data == [
        {
            "uri": "file:///notes.md",
            "name": "notes",
            "server": "alpha",
            "mimeType": "text/markdown",
            "description": "project notes",
        }
    ]
    assert "1 resource(s)" in result.display.text


@pytest.mark.anyio
async def test_list_mcp_resources_reports_unknown_server_names() -> None:
    mcp = MagicMock()
    mcp.get_connected.return_value = [_server("alpha")]

    with pytest.raises(ToolInputError, match='Server "missing" not found'):
        await _collect(ListMcpResourcesTool(), {"server": "missing"}, _ctx(mcp))


@pytest.mark.anyio
async def test_list_mcp_resources_skips_servers_without_resources_and_failed_lists() -> None:
    mcp = MagicMock()
    mcp.get_connected.return_value = [
        _server("tools-only", {"tools": {}}),
        _server("broken", {"resources": {}}),
    ]
    mcp.list_resources = AsyncMock(side_effect=RuntimeError("boom"))

    [result] = await _collect(ListMcpResourcesTool(), {}, _ctx(mcp))

    assert result.data == []
    assert "No resources found" in result.llm_content[0].text


@pytest.mark.anyio
async def test_read_mcp_resource_validates_required_inputs() -> None:
    mcp = MagicMock()
    mcp.get_connected.return_value = []

    with pytest.raises(ToolInputError, match="'server' is required"):
        await _collect(ReadMcpResourceTool(), {"uri": "file:///x"}, _ctx(mcp))
    with pytest.raises(ToolInputError, match="'uri' is required"):
        await _collect(ReadMcpResourceTool(), {"server": "alpha"}, _ctx(mcp))


@pytest.mark.anyio
async def test_read_mcp_resource_rejects_unknown_or_non_resource_server() -> None:
    mcp = MagicMock()
    mcp.get_connected.return_value = [_server("tools-only", {"tools": {}})]

    with pytest.raises(ToolInputError, match='Server "missing" not found'):
        await _collect(
            ReadMcpResourceTool(),
            {"server": "missing", "uri": "file:///x"},
            _ctx(mcp),
        )
    with pytest.raises(ToolInputError, match="does not support resources"):
        await _collect(
            ReadMcpResourceTool(),
            {"server": "tools-only", "uri": "file:///x"},
            _ctx(mcp),
        )


@pytest.mark.anyio
async def test_read_mcp_resource_returns_text_and_binary_content() -> None:
    mcp = MagicMock()
    mcp.get_connected.return_value = [_server("alpha")]
    blob = base64.b64encode(b"PNG").decode()
    mcp.read_resource = AsyncMock(
        return_value=McpResourceResult(
            contents=[
                {"uri": "file:///x.txt", "mimeType": "text/plain", "text": "hello"},
                {"uri": "file:///image.png", "mimeType": "image/png", "blob": blob},
                {"uri": "file:///empty"},
            ]
        )
    )

    [result] = await _collect(
        ReadMcpResourceTool(),
        {"server": "alpha", "uri": "file:///x.txt"},
        _ctx(mcp),
    )

    assert result.data["contents"][0] == {
        "uri": "file:///x.txt",
        "mimeType": "text/plain",
        "text": "hello",
    }
    binary = result.data["contents"][1]
    assert binary["uri"] == "file:///image.png"
    assert binary["mimeType"] == "image/png"
    assert binary["blobSavedTo"].endswith(".png")
    assert Path(binary["blobSavedTo"]).read_bytes() == b"PNG"
    assert result.data["contents"][2] == {"uri": "file:///empty"}
    assert "hello" in result.llm_content[0].text
    assert "Binary content (3 bytes) saved" in result.llm_content[0].text


@pytest.mark.parametrize(
    ("mime", "ext"),
    [
        ("image/webp", ".webp"),
        ("application/pdf", ".pdf"),
        ("application/json", ".json"),
        ("application/unknown-deepcli", ".bin"),
    ],
)
def test_mime_to_ext_fallbacks(mime: str, ext: str) -> None:
    assert _mime_to_ext(mime) == ext
