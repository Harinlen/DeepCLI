"""Tests for the MCP result store backward-compat alias."""

from __future__ import annotations

from daemon.extensions.mcp.result_store import McpResultStore, ResultStore


class TestBackwardCompat:
    """Verify the old import path still works."""

    def test_alias_is_result_store(self) -> None:
        """McpResultStore is an alias for ResultStore."""
        assert McpResultStore is ResultStore
