"""ACP schemas for Kernel-owned `/global` storage commands."""

from __future__ import annotations

from typing import Any, Literal

from kernel.core.protocol.acp.schemas.base import AcpModel


class GlobalBackupRequest(AcpModel):
    actor_agent_id: str = "primary"
    output_dir: str | None = None


class GlobalBackupResponse(AcpModel):
    path: str
    checksum: str
    source_schema_version: int


class GlobalBackupsRequest(AcpModel):
    actor_agent_id: str = "primary"
    backup_dir: str | None = None


class GlobalBackupsResponse(AcpModel):
    backups: list[str]


class GlobalExportRequest(AcpModel):
    actor_agent_id: str = "primary"
    output_path: str | None = None
    dry_run: bool = True
    include_history: bool = False


class GlobalExportResponse(AcpModel):
    dry_run: bool
    format: Literal["json"]
    output_path: str | None
    resource_count: int
    event_count: int
    warnings: list[str]


class GlobalImportRequest(AcpModel):
    actor_agent_id: str = "primary"
    input_path: str
    dry_run: bool = True


class GlobalImportResponse(AcpModel):
    dry_run: bool
    planned_writes: int
    conflicts: list[str]
    errors: list[str]
    warnings: list[str]
    meta: dict[str, Any] | None = None
