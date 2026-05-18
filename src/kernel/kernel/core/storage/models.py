"""Return models for shared SQLite storage operations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    """Generic ResourceStore row."""

    resource_key: str
    payload_json: str
    revision: int
    updated_at: str
    updated_by_agent_id: str | None
    payload_hash: str


@dataclass(frozen=True, slots=True)
class ResourceEvent:
    """Generic ResourceStore event row."""

    id: int
    resource_key: str
    revision: int
    event_type: str
    updated_at: str
    updated_by_agent_id: str | None
    payload_hash: str
    previous_payload_hash: str | None


@dataclass(frozen=True, slots=True)
class SecretRecord:
    """SecretStore metadata row."""

    secret_id: str
    name: str
    revision: int
    created_at: str
    updated_at: str
    created_by_agent_id: str | None
    updated_by_agent_id: str | None
    payload_hash: str


@dataclass(frozen=True, slots=True)
class SecretAuditEvent:
    """SecretStore audit row."""

    id: int
    secret_id: str | None
    event_type: str
    actor_agent_id: str | None
    created_at: str
    metadata_json: str
    payload_hash: str | None
    previous_payload_hash: str | None


@dataclass(frozen=True, slots=True)
class BackupRecord:
    """SQLite backup metadata."""

    path: str
    checksum: str
    created_at: str
    source_schema_version: int


@dataclass(frozen=True, slots=True)
class ImportReport:
    """Import dry-run or apply report."""

    dry_run: bool
    planned_writes: int
    conflicts: tuple[str, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExportReport:
    """ResourceStore export report."""

    dry_run: bool
    format: str
    output_path: str | None
    resource_count: int
    event_count: int
    warnings: tuple[str, ...]
