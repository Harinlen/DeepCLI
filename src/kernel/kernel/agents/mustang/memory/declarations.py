"""ResourceStore-backed global memory declaration policy.

Actual memory entries, indexes, embeddings, recall caches, summaries, and logs
remain filesystem or runtime data.  This module stores only global declaration
metadata: namespace enablement, retention policy, index policy, and disposition
defaults.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson
import yaml

from kernel.core.config.sqlite_backend import ConfigSQLiteBackend
from kernel.core.storage import ResourceStore

MEMORY_DECLARATIONS_FILE = "memory"
MEMORY_DECLARATIONS_SECTION = "global_declarations"
MEMORY_DECLARATIONS_RESOURCE_KEY = "config.global._.memory.global_declarations"
LEGACY_MEMORY_SOURCE_ID = "legacy:memory.config"


DEFAULT_DECLARATION: dict[str, Any] = {
    "enabled": True,
    "namespaces": {
        "global": {
            "enabled": True,
            "categories": ["profile", "semantic", "episodic", "procedural"],
        }
    },
    "retention_policy": {
        "episodic_halflife_days": 30,
        "evergreen_categories": ["profile", "semantic", "procedural"],
    },
    "index_policy": {
        "bm25_enabled": True,
        "llm_scoring_enabled": True,
        "embedding_index_enabled": False,
    },
    "disposition": {"skepticism": 3, "recency_bias": 3, "verbosity": 3},
}


@dataclass(frozen=True, slots=True)
class MemoryDeclarationRecord:
    """ResourceStore-backed global memory declaration snapshot."""

    declaration: dict[str, Any]
    revision: int
    payload_hash: str


@dataclass(frozen=True, slots=True)
class MemoryDeclarationImportReport:
    """Legacy filesystem-to-ResourceStore import outcome."""

    imported: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    drift: tuple[str, ...] = ()
    target_resource_keys: tuple[str, ...] = ()
    dry_run: bool = False


class MemoryDeclarationStore:
    """Memory-owned facade over ResourceStore global memory declarations."""

    def __init__(self, store: ResourceStore) -> None:
        self._store = store
        self._backend = ConfigSQLiteBackend(store)

    @classmethod
    def open(cls, home: Path) -> "MemoryDeclarationStore":
        return cls(ResourceStore.open(home))

    def read_global(self) -> MemoryDeclarationRecord | None:
        row = self._backend.read(
            file=MEMORY_DECLARATIONS_FILE,
            section=MEMORY_DECLARATIONS_SECTION,
        )
        if row is None:
            return None
        return MemoryDeclarationRecord(
            declaration=_normalize_declaration(row.payload),
            revision=row.revision,
            payload_hash=row.payload_hash,
        )

    def write_global(
        self,
        declaration: dict[str, Any],
        *,
        expected_revision: int | None,
        actor: str | None = None,
    ) -> MemoryDeclarationRecord:
        row = self._backend.write(
            file=MEMORY_DECLARATIONS_FILE,
            section=MEMORY_DECLARATIONS_SECTION,
            payload=_normalize_declaration(declaration),
            expected_revision=expected_revision,
            actor=actor,
        )
        return MemoryDeclarationRecord(
            declaration=_normalize_declaration(row.payload),
            revision=row.revision,
            payload_hash=row.payload_hash,
        )

    def ensure_default_global(self, *, actor: str = "system") -> MemoryDeclarationRecord:
        record = self.read_global()
        if record is not None:
            return record
        return self.write_global(DEFAULT_DECLARATION, expected_revision=None, actor=actor)

    def import_legacy_config(
        self,
        memory_root: Path,
        *,
        actor: str = "system",
        dry_run: bool = False,
    ) -> MemoryDeclarationImportReport:
        """Import global ``memory/config.md`` once; report later drift only."""
        config_path = memory_root / "config.md"
        if not config_path.exists():
            return MemoryDeclarationImportReport(dry_run=dry_run)
        declaration = _legacy_config_to_declaration(config_path)
        source_hash = _hash_declaration(declaration)
        marker = self._read_marker(LEGACY_MEMORY_SOURCE_ID)
        if marker is not None:
            if marker["source_hash"] == source_hash:
                return MemoryDeclarationImportReport(
                    skipped=(LEGACY_MEMORY_SOURCE_ID,),
                    target_resource_keys=(MEMORY_DECLARATIONS_RESOURCE_KEY,),
                    dry_run=dry_run,
                )
            return MemoryDeclarationImportReport(
                drift=(LEGACY_MEMORY_SOURCE_ID,),
                target_resource_keys=(MEMORY_DECLARATIONS_RESOURCE_KEY,),
                dry_run=dry_run,
            )

        if not dry_run:
            self.write_global(declaration, expected_revision=None, actor=actor)
            self._write_marker(
                source_id=LEGACY_MEMORY_SOURCE_ID,
                source_path=config_path,
                source_hash=source_hash,
                source_kind="memory",
                imported_by=actor,
                target_resource_keys=(MEMORY_DECLARATIONS_RESOURCE_KEY,),
            )
        return MemoryDeclarationImportReport(
            imported=(LEGACY_MEMORY_SOURCE_ID,),
            target_resource_keys=(MEMORY_DECLARATIONS_RESOURCE_KEY,),
            dry_run=dry_run,
        )

    def close(self) -> None:
        self._store.close()

    def _read_marker(self, source_id: str) -> dict[str, Any] | None:
        row = self._store.read_tx(
            lambda conn: conn.execute(
                "SELECT source_id, source_hash FROM migration_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        )
        return {str(key): value for key, value in row.items()} if row is not None else None

    def _write_marker(
        self,
        *,
        source_id: str,
        source_path: Path,
        source_hash: str,
        source_kind: str,
        imported_by: str,
        target_resource_keys: tuple[str, ...],
    ) -> None:
        self._store.write_tx(
            lambda conn: conn.execute(
                """
                INSERT INTO migration_sources (
                    source_id, source_path, source_hash, source_kind, imported_at,
                    imported_by, target_resource_keys_json, report_json
                )
                VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?)
                """,
                (
                    source_id,
                    str(source_path),
                    source_hash,
                    source_kind,
                    imported_by,
                    orjson.dumps(target_resource_keys).decode(),
                    "{}",
                ),
            )
        )


def _legacy_config_to_declaration(config_path: Path) -> dict[str, Any]:
    text = config_path.read_text(encoding="utf-8")
    frontmatter = _parse_frontmatter(text)
    declaration = dict(DEFAULT_DECLARATION)
    disposition = dict(declaration["disposition"])
    for key in ("skepticism", "recency_bias", "verbosity"):
        value = frontmatter.get(key)
        if isinstance(value, int) and 1 <= value <= 5:
            disposition[key] = value
    declaration["disposition"] = disposition
    return declaration


def _normalize_declaration(payload: dict[str, Any]) -> dict[str, Any]:
    declaration = orjson.loads(orjson.dumps(DEFAULT_DECLARATION))
    for key, value in payload.items():
        if key in {"enabled", "namespaces", "retention_policy", "index_policy", "disposition"}:
            declaration[key] = value
    return declaration


def _parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        parsed = yaml.safe_load(text[3:end].strip()) or {}
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _hash_declaration(declaration: dict[str, Any]) -> str:
    return hashlib.sha256(orjson.dumps(declaration, option=orjson.OPT_SORT_KEYS)).hexdigest()
