"""Mustang command-catalog ACP extension schemas."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from kernel.core.protocol.acp.schemas.base import AcpModel


class CommandEntry(AcpModel):
    """One slash command exposed to clients for autocomplete/help."""

    name: str
    description: str
    usage: str
    acp_method: str | None = None
    subcommands: list[str] = Field(default_factory=list)
    source: str = "builtin"


class ListCommandsRequest(AcpModel):
    """Request the current Kernel-owned slash command catalog."""

    meta: dict[str, Any] | None = None


class ListCommandsResponse(AcpModel):
    """Full command catalog snapshot."""

    commands: list[CommandEntry]
    meta: dict[str, Any] | None = None
