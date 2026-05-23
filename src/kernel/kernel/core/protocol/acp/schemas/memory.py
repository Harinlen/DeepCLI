"""ACP schemas for memory slash-command management."""

from __future__ import annotations

from typing import Any

from kernel.core.protocol.acp.schemas.base import AcpModel


class MemoryListRequest(AcpModel):
    category: str | None = None


class MemoryListResponse(AcpModel):
    memories: list[dict[str, Any]]


class MemoryShowRequest(AcpModel):
    name: str


class MemoryShowResponse(AcpModel):
    memory: dict[str, Any]


class MemoryDeleteRequest(AcpModel):
    name: str
    confirm: bool = False


class MemoryDeleteResponse(AcpModel):
    name: str
    filename: str
    deleted: bool
