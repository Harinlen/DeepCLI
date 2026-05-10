"""ACP initialize / authenticate wire-format schemas."""

from __future__ import annotations

from typing import Any

from kernel.core.protocol.acp.schemas.base import AcpModel


class AcpImplementation(AcpModel):
    """``clientInfo`` / ``agentInfo`` fields."""

    name: str
    title: str | None = None
    version: str | None = None


class AcpFsCapabilities(AcpModel):
    read_text_file: bool = False
    write_text_file: bool = False


class AcpClientCapabilities(AcpModel):
    fs: AcpFsCapabilities = AcpFsCapabilities()
    terminal: bool = False
    meta: dict[str, Any] | None = None


class AcpPromptCapabilities(AcpModel):
    image: bool = False
    audio: bool = False
    embedded_context: bool = False


class AcpMcpCapabilities(AcpModel):
    http: bool = False
    sse: bool = False


class AcpSessionCapabilities(AcpModel):
    list: dict | None = None
    """Non-null (even if empty dict) signals ``session/list`` is supported."""
    close: dict | None = None
    """Non-null signals ``session/close`` is supported."""
    resume: dict | None = None
    """Non-null signals ``session/resume`` is supported."""
    meta: dict[str, Any] | None = None


class AcpAgentCapabilities(AcpModel):
    load_session: bool = False
    prompt_capabilities: AcpPromptCapabilities = AcpPromptCapabilities()
    mcp_capabilities: AcpMcpCapabilities = AcpMcpCapabilities()
    session_capabilities: AcpSessionCapabilities = AcpSessionCapabilities()
    meta: dict[str, Any] | None = None


class InitializeRequest(AcpModel):
    protocol_version: int
    client_capabilities: AcpClientCapabilities = AcpClientCapabilities()
    client_info: AcpImplementation | None = None
    meta: dict[str, Any] | None = None


class InitializeResponse(AcpModel):
    protocol_version: int
    agent_capabilities: AcpAgentCapabilities = AcpAgentCapabilities()
    agent_info: AcpImplementation | None = None
    auth_methods: list[dict] = []
    meta: dict[str, Any] | None = None


class AuthenticateRequest(AcpModel):
    method_id: str
    meta: dict[str, Any] | None = None


class AuthenticateResponse(AcpModel):
    meta: dict[str, Any] | None = None
