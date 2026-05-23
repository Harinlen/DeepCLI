"""ACP schemas for cron slash-command management."""

from __future__ import annotations

from typing import Any

from kernel.core.protocol.acp.schemas.base import AcpModel


class CronListRequest(AcpModel):
    include_completed: bool = False


class CronListResponse(AcpModel):
    jobs: list[dict[str, Any]]


class CronCreateRequest(AcpModel):
    schedule: str
    prompt: str
    description: str | None = None
    recurring: bool | None = None


class CronCreateResponse(AcpModel):
    job: dict[str, Any]


class CronDeleteRequest(AcpModel):
    id: str


class CronDeleteResponse(AcpModel):
    id: str
    deleted: bool
