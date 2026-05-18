"""Legacy YAML import helpers for ResourceStore-backed global config."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import orjson
import yaml

from kernel.core.config.sqlite_backend import ConfigSQLiteBackend
from kernel.core.flags.sqlite_backend import FlagSQLiteBackend
from kernel.core.storage import ResourceStore

_SECRET_NAME_REF_RE = re.compile(r"\$\{secret:([^}]+)\}")
_UUIDISH_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True, slots=True)
class LegacyMigrationReport:
    """Report for known legacy YAML import."""

    imported: tuple[str, ...]
    skipped: tuple[str, ...]
    drift: tuple[str, ...]
    manual_actions: tuple[str, ...]
    target_resource_keys: tuple[str, ...]
    dry_run: bool


def apply_legacy_yaml_import(
    home: Path,
    *,
    dry_run: bool = False,
    actor: str = "system",
) -> LegacyMigrationReport:
    """Import known ``kernel.yaml`` and ``flags.yaml`` once."""
    store = ResourceStore.open(home)
    try:
        imported: list[str] = []
        skipped: list[str] = []
        drift: list[str] = []
        manual_actions: list[str] = []
        targets: list[str] = []

        for result in (
            _import_config(store, home / "config" / "kernel.yaml", dry_run=dry_run, actor=actor),
            _import_flags(store, home / "config" / "flags.yaml", dry_run=dry_run, actor=actor),
        ):
            _merge_result(result, imported, skipped, drift, manual_actions, targets)

        return LegacyMigrationReport(
            imported=tuple(imported),
            skipped=tuple(skipped),
            drift=tuple(drift),
            manual_actions=tuple(manual_actions),
            target_resource_keys=tuple(targets),
            dry_run=dry_run,
        )
    finally:
        store.close()


def _import_config(
    store: ResourceStore,
    path: Path,
    *,
    dry_run: bool,
    actor: str,
) -> LegacyMigrationReport:
    if not path.exists():
        return _single(dry_run=dry_run)
    source_id = "legacy:kernel.yaml"
    source_hash = _file_hash(path)
    marker = _read_marker(store, source_id)
    if marker is not None:
        if marker["source_hash"] == source_hash:
            return _single(skipped=(source_id,), dry_run=dry_run)
        return _single(drift=(source_id,), dry_run=dry_run)

    data = _read_yaml_mapping(path)
    manual_actions = tuple(_find_manual_secret_refs(data))
    targets = tuple(f"config.global._.kernel.{section}" for section in data)
    if not dry_run:
        backend = ConfigSQLiteBackend(store)
        for section, payload in data.items():
            if isinstance(payload, dict):
                backend.write(
                    file="kernel",
                    section=str(section),
                    payload=payload,
                    expected_revision=None,
                    actor=actor,
                )
        _write_marker(
            store,
            source_id=source_id,
            source_path=path,
            source_hash=source_hash,
            source_kind="config",
            imported_by=actor,
            target_resource_keys=targets,
            report={"manual_actions": manual_actions},
        )
    return _single(
        imported=(source_id,),
        manual_actions=manual_actions,
        targets=targets,
        dry_run=dry_run,
    )


def _import_flags(
    store: ResourceStore,
    path: Path,
    *,
    dry_run: bool,
    actor: str,
) -> LegacyMigrationReport:
    if not path.exists():
        return _single(dry_run=dry_run)
    source_id = "legacy:flags.yaml"
    source_hash = _file_hash(path)
    marker = _read_marker(store, source_id)
    if marker is not None:
        if marker["source_hash"] == source_hash:
            return _single(skipped=(source_id,), dry_run=dry_run)
        return _single(drift=(source_id,), dry_run=dry_run)

    data = _read_yaml_mapping(path)
    targets = tuple(f"flags.{section}" for section in data)
    if not dry_run:
        backend = FlagSQLiteBackend(store)
        for section, payload in data.items():
            if isinstance(payload, dict):
                backend.write(
                    section=str(section),
                    payload=payload,
                    expected_revision=None,
                    actor=actor,
                )
        _write_marker(
            store,
            source_id=source_id,
            source_path=path,
            source_hash=source_hash,
            source_kind="flags",
            imported_by=actor,
            target_resource_keys=targets,
            report={},
        )
    return _single(imported=(source_id,), targets=targets, dry_run=dry_run)


def _read_marker(store: ResourceStore, source_id: str) -> dict[str, Any] | None:
    row = store.read_tx(
        lambda conn: conn.execute(
            "SELECT source_id, source_hash FROM migration_sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    )
    return {str(key): value for key, value in row.items()} if row is not None else None


def _write_marker(
    store: ResourceStore,
    *,
    source_id: str,
    source_path: Path,
    source_hash: str,
    source_kind: str,
    imported_by: str,
    target_resource_keys: tuple[str, ...],
    report: dict[str, object],
) -> None:
    store.write_tx(
        lambda conn: conn.execute(
            """
            INSERT INTO migration_sources (
                source_id, source_path, source_hash, source_kind, imported_at,
                imported_by, target_resource_keys_json, report_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                str(source_path),
                source_hash,
                source_kind,
                _now_iso(),
                imported_by,
                orjson.dumps(target_resource_keys).decode(),
                orjson.dumps(report, option=orjson.OPT_SORT_KEYS).decode(),
            ),
        )
    )


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def _find_manual_secret_refs(data: object) -> list[str]:
    refs: list[str] = []
    if isinstance(data, dict):
        for value in data.values():
            refs.extend(_find_manual_secret_refs(value))
    elif isinstance(data, list):
        for value in data:
            refs.extend(_find_manual_secret_refs(value))
    elif isinstance(data, str):
        for match in _SECRET_NAME_REF_RE.finditer(data):
            ref = match.group(1)
            if not _UUIDISH_RE.match(ref.removeprefix("secret:")):
                refs.append(f"manual_secret_reference:{ref}")
    return refs


def _merge_result(
    result: LegacyMigrationReport,
    imported: list[str],
    skipped: list[str],
    drift: list[str],
    manual_actions: list[str],
    targets: list[str],
) -> None:
    imported.extend(result.imported)
    skipped.extend(result.skipped)
    drift.extend(result.drift)
    manual_actions.extend(result.manual_actions)
    targets.extend(result.target_resource_keys)


def _single(
    *,
    imported: tuple[str, ...] = (),
    skipped: tuple[str, ...] = (),
    drift: tuple[str, ...] = (),
    manual_actions: tuple[str, ...] = (),
    targets: tuple[str, ...] = (),
    dry_run: bool,
) -> LegacyMigrationReport:
    return LegacyMigrationReport(
        imported=imported,
        skipped=skipped,
        drift=drift,
        manual_actions=manual_actions,
        target_resource_keys=targets,
        dry_run=dry_run,
    )


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
