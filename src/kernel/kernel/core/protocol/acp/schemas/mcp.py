"""ACP schemas for Kernel-owned MCP declaration management methods."""

from __future__ import annotations

from typing import Any, Literal

from kernel.core.protocol.acp.schemas.base import AcpModel


class MCPListRequest(AcpModel):
    actor_agent_id: str = "primary"


class MCPServerEntry(AcpModel):
    name: str
    type: str
    config: dict[str, Any]


class MCPListResponse(AcpModel):
    servers: list[MCPServerEntry]
    revision: int


class MCPReadRequest(AcpModel):
    actor_agent_id: str = "primary"
    name: str


class MCPReadResponse(AcpModel):
    server: MCPServerEntry
    revision: int


class MCPWriteRequest(AcpModel):
    actor_agent_id: str = "primary"
    name: str
    config: dict[str, Any]
    expected_revision: int | None = None


class MCPWriteResponse(AcpModel):
    server: MCPServerEntry
    revision: int
    applies: Literal["after_restart"]
    pending_restart: bool


class MCPDeleteRequest(AcpModel):
    actor_agent_id: str = "primary"
    name: str
    expected_revision: int | None = None


class MCPDeleteResponse(AcpModel):
    name: str
    deleted: bool
    revision: int
    applies: Literal["after_restart"]
    pending_restart: bool
