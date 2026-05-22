"""ACP schemas for Kernel-owned `/secrets` management methods."""

from __future__ import annotations

from typing import Any

from kernel.core.protocol.acp.schemas.base import AcpModel


class SecretsListRequest(AcpModel):
    actor_agent_id: str = "primary"


class SecretMetaEntry(AcpModel):
    secret_id: str
    name: str
    revision: int
    created_at: str
    updated_at: str


class SecretsListResponse(AcpModel):
    secrets: list[SecretMetaEntry]


class SecretsAuditRequest(AcpModel):
    actor_agent_id: str = "primary"
    secret_id: str | None = None


class SecretAuditEntry(AcpModel):
    id: int
    secret_id: str | None
    event_type: str
    actor_agent_id: str | None
    created_at: str
    metadata: dict[str, Any]


class SecretsAuditResponse(AcpModel):
    events: list[SecretAuditEntry]


class SecretsRenameRequest(AcpModel):
    actor_agent_id: str = "primary"
    secret_id: str
    name: str
    expected_revision: int


class SecretsRenameResponse(AcpModel):
    secret_id: str
    ref: str
    name: str
    revision: int


class SecretsDeleteRequest(AcpModel):
    actor_agent_id: str = "primary"
    secret_id: str
    expected_revision: int
    confirm: bool = False


class SecretsDeleteResponse(AcpModel):
    deleted: bool
