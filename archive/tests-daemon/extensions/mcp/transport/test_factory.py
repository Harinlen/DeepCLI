"""Tests for the transport factory function."""

from __future__ import annotations

import pytest

from daemon.extensions.mcp.config import McpServerEntry
from daemon.extensions.mcp.transport import (
    UnsupportedTransport,
    create_transport,
)
from daemon.extensions.mcp.transport.stdio import StdioTransport


class TestCreateTransport:
    """Tests for create_transport() dispatch."""

    def test_stdio_returns_stdio_transport(self) -> None:
        entry = McpServerEntry(name="s", type="stdio", command="echo")
        transport = create_transport(entry)
        assert isinstance(transport, StdioTransport)

    def test_unsupported_type_raises(self) -> None:
        entry = McpServerEntry(name="s", type="grpc", command="echo")
        with pytest.raises(UnsupportedTransport, match="grpc"):
            create_transport(entry)

    def test_empty_type_raises(self) -> None:
        entry = McpServerEntry(name="s", type="", command="echo")
        with pytest.raises(UnsupportedTransport):
            create_transport(entry)
