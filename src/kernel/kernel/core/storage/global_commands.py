"""Kernel-owned `/global` storage command surface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import orjson

from kernel.core.storage import ResourceStore
from kernel.core.storage.models import BackupRecord, ExportReport, ImportReport
from kernel.core.storage.sqlalchemy_async import make_engine, run_async


class GlobalAuthorizationError(PermissionError):
    """Raised when an Agent is not allowed to run global storage commands."""


class GlobalRestoreUnavailable(RuntimeError):
    """Raised when online restore/import apply is requested."""


@dataclass(frozen=True, slots=True)
class GlobalBackupList:
    """Backups visible to the `/global backups` command."""

    backups: tuple[str, ...]


class GlobalResourceCommandService:
    """Primary-only command service for ResourceStore backup/export/import."""

    def __init__(self, home: Path) -> None:
        self._home = home

    def backup(self, *, actor_agent_id: str, output_dir: Path | None = None) -> BackupRecord:
        self._require_primary(actor_agent_id)
        store = ResourceStore.open(self._home)
        try:
            directory = output_dir or (self._home / "backups")
            output = directory / f"global-{store.schema_version}.db"
            return store.backup(output)
        finally:
            store.close()

    def backups(self, *, actor_agent_id: str, backup_dir: Path | None = None) -> GlobalBackupList:
        self._require_primary(actor_agent_id)
        directory = backup_dir or (self._home / "backups")
        if not directory.exists():
            return GlobalBackupList(backups=())
        return GlobalBackupList(
            backups=tuple(str(path) for path in sorted(directory.glob("global-*.db")))
        )

    def export(
        self,
        *,
        actor_agent_id: str,
        output_path: Path | None = None,
        dry_run: bool = True,
        include_history: bool = False,
    ) -> ExportReport:
        self._require_primary(actor_agent_id)
        store = ResourceStore.open(self._home)
        try:
            return store.export(
                "json",
                output_path,
                include_history=include_history,
                dry_run=dry_run,
            )
        finally:
            store.close()

    def import_dry_run(self, *, actor_agent_id: str, input_path: Path) -> ImportReport:
        self._require_primary(actor_agent_id)
        payload = orjson.loads(input_path.read_bytes())
        resources = payload.get("resources", [])
        if not isinstance(resources, list):
            return ImportReport(
                dry_run=True,
                planned_writes=0,
                conflicts=(),
                errors=("resources must be a list",),
                warnings=(),
            )

        store = ResourceStore.open(self._home)
        try:
            conflicts: list[str] = []
            planned = 0
            for row in resources:
                if not isinstance(row, dict) or "resource_key" not in row:
                    conflicts.append("invalid_resource_row")
                    continue
                resource_key = str(row["resource_key"])
                existing = store.get_resource(resource_key)
                if existing is None:
                    planned += 1
                    continue
                incoming_hash = str(row.get("payload_hash", ""))
                if incoming_hash and incoming_hash != existing.payload_hash:
                    conflicts.append(resource_key)
            return ImportReport(
                dry_run=True,
                planned_writes=planned,
                conflicts=tuple(conflicts),
                errors=(),
                warnings=("apply unavailable while runtimes may write",),
            )
        finally:
            store.close()

    def import_apply(self, *, actor_agent_id: str, input_path: Path) -> ImportReport:
        del input_path
        self._require_primary(actor_agent_id)
        raise GlobalRestoreUnavailable("online global import/restore is deferred")

    @staticmethod
    def _require_primary(actor_agent_id: str) -> None:
        if actor_agent_id != "primary":
            raise GlobalAuthorizationError("only primary Agent may run /global commands")


def read_sqlite_user_version(path: Path) -> int:
    """Read a SQLite user_version from a backup path through SQLAlchemy asyncio."""

    async def _read() -> int:
        engine = make_engine(path)
        try:
            async with engine.connect() as conn:
                row = (await conn.exec_driver_sql("PRAGMA user_version")).fetchone()
                return int(row[0]) if row is not None else 0
        finally:
            await engine.dispose()

    return run_async(_read)
