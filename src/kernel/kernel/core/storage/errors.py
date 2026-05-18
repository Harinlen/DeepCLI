"""Domain errors raised by shared SQLite storage libraries."""

from __future__ import annotations


class StoreError(RuntimeError):
    """Base class for ResourceStore and SecretStore failures."""


class StoreOpenError(StoreError):
    """Raised when a store database cannot be opened or configured."""


class StoreMigrationError(StoreError):
    """Raised when schema migration cannot reach the expected version."""


class StoreBusyTimeout(StoreError):
    """Raised when SQLite cannot acquire a lock before the busy timeout."""


class RevisionConflict(StoreError):
    """Raised when an optimistic revision check fails."""

    def __init__(
        self,
        message: str,
        *,
        resource_key: str,
        current_revision: int | None,
        current_hash: str | None,
    ) -> None:
        super().__init__(message)
        self.resource_key = resource_key
        self.current_revision = current_revision
        self.current_hash = current_hash


class SchemaValidationError(StoreError):
    """Raised when a resource payload fails schema validation."""


class BackupError(StoreError):
    """Raised when a SQLite backup operation fails."""


class ImportConflict(StoreError):
    """Raised when an import would overwrite conflicting durable state."""
