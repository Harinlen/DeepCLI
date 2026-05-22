"""ResourceStore-backed global prompt declaration index.

Prompt text remains file-backed content loaded by PromptManager.  This module
persists only declaration metadata: prompt ids, source paths, enabled state,
and lightweight template metadata.
"""

from __future__ import annotations

import hashlib
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from kernel.core.config.sqlite_backend import ConfigSQLiteBackend
from kernel.core.storage import ResourceStore

PROMPT_DECLARATIONS_FILE = "prompts"
PROMPT_DECLARATIONS_SECTION = "global_declarations"
PROMPT_DECLARATIONS_RESOURCE_KEY = "config.global._.prompts.global_declarations"
LEGACY_PROMPT_SOURCE_ID = "legacy:prompts.user_manifest"


@dataclass(frozen=True, slots=True)
class PromptDeclarationRecord:
    """ResourceStore-backed global prompt declaration snapshot."""

    prompts: tuple[dict[str, Any], ...]
    revision: int
    payload_hash: str


@dataclass(frozen=True, slots=True)
class PromptDeclarationImportReport:
    """Legacy filesystem-to-ResourceStore import outcome."""

    imported: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    drift: tuple[str, ...] = ()
    target_resource_keys: tuple[str, ...] = ()
    dry_run: bool = False


class PromptDeclarationStore:
    """Prompt-owned facade over ResourceStore global prompt declarations."""

    def __init__(self, store: ResourceStore) -> None:
        self._store = store
        self._backend = ConfigSQLiteBackend(store)

    @classmethod
    def open(cls, home: Path) -> "PromptDeclarationStore":
        return cls(ResourceStore.open(home))

    def read_global(self) -> PromptDeclarationRecord | None:
        row = self._backend.read(
            file=PROMPT_DECLARATIONS_FILE,
            section=PROMPT_DECLARATIONS_SECTION,
        )
        if row is None:
            return None
        return PromptDeclarationRecord(
            prompts=tuple(row.payload.get("prompts", ())),
            revision=row.revision,
            payload_hash=row.payload_hash,
        )

    def write_global(
        self,
        prompts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        *,
        expected_revision: int | None,
        actor: str | None = None,
    ) -> PromptDeclarationRecord:
        payload = {"prompts": [_normalize_prompt(item) for item in prompts]}
        row = self._backend.write(
            file=PROMPT_DECLARATIONS_FILE,
            section=PROMPT_DECLARATIONS_SECTION,
            payload=payload,
            expected_revision=expected_revision,
            actor=actor,
        )
        return PromptDeclarationRecord(
            prompts=tuple(row.payload.get("prompts", ())),
            revision=row.revision,
            payload_hash=row.payload_hash,
        )

    def import_legacy_user_prompts(
        self,
        prompts_dir: Path,
        *,
        actor: str = "system",
        dry_run: bool = False,
    ) -> PromptDeclarationImportReport:
        """Import global user prompt declaration metadata once."""
        declarations = discover_prompt_declarations(prompts_dir)
        source_hash = _hash_declarations(declarations)
        marker = self._read_marker(LEGACY_PROMPT_SOURCE_ID)
        if marker is not None:
            if marker["source_hash"] == source_hash:
                return PromptDeclarationImportReport(
                    skipped=(LEGACY_PROMPT_SOURCE_ID,),
                    target_resource_keys=(PROMPT_DECLARATIONS_RESOURCE_KEY,),
                    dry_run=dry_run,
                )
            return PromptDeclarationImportReport(
                drift=(LEGACY_PROMPT_SOURCE_ID,),
                target_resource_keys=(PROMPT_DECLARATIONS_RESOURCE_KEY,),
                dry_run=dry_run,
            )

        if not dry_run:
            self.write_global(declarations, expected_revision=None, actor=actor)
            self._write_marker(
                source_id=LEGACY_PROMPT_SOURCE_ID,
                source_path=prompts_dir,
                source_hash=source_hash,
                source_kind="prompts",
                imported_by=actor,
                target_resource_keys=(PROMPT_DECLARATIONS_RESOURCE_KEY,),
            )
        return PromptDeclarationImportReport(
            imported=(LEGACY_PROMPT_SOURCE_ID,),
            target_resource_keys=(PROMPT_DECLARATIONS_RESOURCE_KEY,),
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


def discover_prompt_declarations(root: Path) -> list[dict[str, Any]]:
    """Return prompt declaration metadata for ``*.txt`` files under *root*."""
    if not root.is_dir():
        return []
    declarations: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.txt")):
        key = path.relative_to(root).with_suffix("").as_posix()
        text = path.read_text(encoding="utf-8")
        fields = tuple(
            sorted(
                field_name
                for _, field_name, _, _ in string.Formatter().parse(text)
                if field_name
            )
        )
        declarations.append(
            {
                "key": key,
                "enabled": True,
                "source": "global_user",
                "source_path": str(path),
                "has_placeholders": bool(fields),
                "placeholders": list(fields),
            }
        )
    return declarations


def _normalize_prompt(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": str(payload["key"]),
        "enabled": bool(payload.get("enabled", True)),
        "source": str(payload.get("source", "global_user")),
        "source_path": str(payload.get("source_path", "")),
        "has_placeholders": bool(payload.get("has_placeholders", False)),
        "placeholders": list(payload.get("placeholders") or ()),
    }


def _hash_declarations(declarations: list[dict[str, Any]]) -> str:
    normalized = [_normalize_prompt(item) for item in declarations]
    return hashlib.sha256(orjson.dumps(normalized, option=orjson.OPT_SORT_KEYS)).hexdigest()
