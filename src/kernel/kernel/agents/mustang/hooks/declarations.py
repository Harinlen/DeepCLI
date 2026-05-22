"""ResourceStore-backed global hook declaration index.

Hook handlers are trusted Python files loaded from disk at runtime.  This
module persists only declaration metadata: manifest fields, trigger bindings,
enabled state, and handler path pointers.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from kernel.agents.mustang.hooks.loader import LoadedHook, _discover_layer, _import_handler
from kernel.agents.mustang.hooks.manifest import HookManifest, HookRequires
from kernel.agents.mustang.hooks.types import HookEvent
from kernel.core.config.sqlite_backend import ConfigSQLiteBackend
from kernel.core.storage import ResourceStore

HOOK_DECLARATIONS_FILE = "hooks"
HOOK_DECLARATIONS_SECTION = "global_declarations"
HOOK_DECLARATIONS_RESOURCE_KEY = "config.global._.hooks.global_declarations"
LEGACY_HOOK_SOURCE_ID = "legacy:hooks.user_manifest"


@dataclass(frozen=True, slots=True)
class HookDeclarationRecord:
    """ResourceStore-backed global hook declaration snapshot."""

    hooks: tuple[LoadedHook, ...]
    revision: int
    payload_hash: str


@dataclass(frozen=True, slots=True)
class HookDeclarationImportReport:
    """Legacy filesystem-to-ResourceStore import outcome."""

    imported: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    drift: tuple[str, ...] = ()
    target_resource_keys: tuple[str, ...] = ()
    dry_run: bool = False


class HookDeclarationStore:
    """Hook-owned facade over ResourceStore global hook declarations."""

    def __init__(self, store: ResourceStore) -> None:
        self._store = store
        self._backend = ConfigSQLiteBackend(store)

    @classmethod
    def open(cls, home: Path) -> "HookDeclarationStore":
        return cls(ResourceStore.open(home))

    def read_global(self) -> HookDeclarationRecord | None:
        row = self._backend.read(file=HOOK_DECLARATIONS_FILE, section=HOOK_DECLARATIONS_SECTION)
        if row is None:
            return None
        return HookDeclarationRecord(
            hooks=tuple(
                hook for item in row.payload.get("hooks", []) if (hook := _hook_from_payload(item))
            ),
            revision=row.revision,
            payload_hash=row.payload_hash,
        )

    def write_global(
        self,
        hooks: list[LoadedHook] | tuple[LoadedHook, ...],
        *,
        expected_revision: int | None,
        actor: str | None = None,
    ) -> HookDeclarationRecord:
        payload = {"hooks": [_hook_to_payload(hook) for hook in hooks]}
        row = self._backend.write(
            file=HOOK_DECLARATIONS_FILE,
            section=HOOK_DECLARATIONS_SECTION,
            payload=payload,
            expected_revision=expected_revision,
            actor=actor,
        )
        return HookDeclarationRecord(
            hooks=tuple(
                hook for item in row.payload.get("hooks", []) if (hook := _hook_from_payload(item))
            ),
            revision=row.revision,
            payload_hash=row.payload_hash,
        )

    def import_legacy_user_hooks(
        self,
        user_hooks_dir: Path,
        *,
        actor: str = "system",
        dry_run: bool = False,
    ) -> HookDeclarationImportReport:
        """Import user/global filesystem hook declarations once."""
        discovered = _discover_layer(base_dir=user_hooks_dir, layer="user", opt_in=None)
        source_hash = _hash_hooks(discovered)
        marker = self._read_marker(LEGACY_HOOK_SOURCE_ID)
        if marker is not None:
            if marker["source_hash"] == source_hash:
                return HookDeclarationImportReport(
                    skipped=(LEGACY_HOOK_SOURCE_ID,),
                    target_resource_keys=(HOOK_DECLARATIONS_RESOURCE_KEY,),
                    dry_run=dry_run,
                )
            return HookDeclarationImportReport(
                drift=(LEGACY_HOOK_SOURCE_ID,),
                target_resource_keys=(HOOK_DECLARATIONS_RESOURCE_KEY,),
                dry_run=dry_run,
            )

        if not dry_run:
            self.write_global(discovered, expected_revision=None, actor=actor)
            self._write_marker(
                source_id=LEGACY_HOOK_SOURCE_ID,
                source_path=user_hooks_dir,
                source_hash=source_hash,
                source_kind="hooks",
                imported_by=actor,
                target_resource_keys=(HOOK_DECLARATIONS_RESOURCE_KEY,),
            )
        return HookDeclarationImportReport(
            imported=(LEGACY_HOOK_SOURCE_ID,),
            target_resource_keys=(HOOK_DECLARATIONS_RESOURCE_KEY,),
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


def _hook_to_payload(hook: LoadedHook) -> dict[str, Any]:
    manifest = hook.manifest
    return {
        "manifest": {
            "name": manifest.name,
            "description": manifest.description,
            "events": list(manifest.events),
            "requires": {
                "bins": list(manifest.requires.bins),
                "env": list(manifest.requires.env),
            },
            "os": list(manifest.os),
            "base_dir": str(manifest.base_dir),
            "handler_path": str(manifest.handler_path),
        },
        "layer": hook.layer,
        "enabled": True,
    }


def _hook_from_payload(payload: dict[str, Any]) -> LoadedHook | None:
    raw = payload["manifest"]
    manifest = HookManifest(
        name=str(raw["name"]),
        description=str(raw.get("description") or ""),
        events=tuple(raw.get("events") or ()),
        requires=HookRequires(
            bins=tuple((raw.get("requires") or {}).get("bins") or ()),
            env=tuple((raw.get("requires") or {}).get("env") or ()),
        ),
        os=tuple(raw.get("os") or ()),
        base_dir=Path(raw["base_dir"]),
        handler_path=Path(raw["handler_path"]),
    )
    try:
        events = tuple(HookEvent(event) for event in manifest.events)
    except ValueError:
        return None
    handler = _import_handler(manifest, layer=str(payload.get("layer", "user")))
    if handler is None:
        return None
    return LoadedHook(
        manifest=manifest,
        handler=handler,
        layer=str(payload.get("layer", "user")),
        events=events,
    )


def _hash_hooks(hooks: list[LoadedHook]) -> str:
    payload = [_hook_to_payload(hook) for hook in sorted(hooks, key=lambda item: item.manifest.name)]
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()
