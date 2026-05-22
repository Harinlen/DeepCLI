"""ACP schemas for Kernel-owned `/flags` management methods."""

from __future__ import annotations

from typing import Any, Literal

from kernel.core.protocol.acp.schemas.base import AcpModel


class FlagsListRequest(AcpModel):
    actor_agent_id: str = "primary"


class FlagSectionEntry(AcpModel):
    section: str
    payload: dict[str, Any]
    revision: int | None = None
    pending_restart: bool = False


class FlagsListResponse(AcpModel):
    sections: list[FlagSectionEntry]


class FlagsReadRequest(AcpModel):
    actor_agent_id: str = "primary"
    section: str


class FlagsReadResponse(FlagSectionEntry):
    pass


class FlagsSetRequest(AcpModel):
    actor_agent_id: str = "primary"
    section: str
    key: str
    value: Any
    expected_revision: int | None = None


class FlagsResetRequest(AcpModel):
    actor_agent_id: str = "primary"
    section: str
    key: str | None = None
    expected_revision: int | None = None


class FlagsWriteResponse(AcpModel):
    section: str
    revision: int
    applies: Literal["after_restart"]
    pending_restart: bool
