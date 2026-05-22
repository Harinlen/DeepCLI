"""Closure probe for ResourceStore-backed global memory declarations."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import orjson

from kernel.agents.mustang.memory import MemoryManager, store
from kernel.agents.mustang.memory.declarations import DEFAULT_DECLARATION, MemoryDeclarationStore
from kernel.agents.mustang.memory.types import MemoryHeader
from kernel.agents.mustang.module_table import KernelModuleTable
from kernel.core.config import ConfigManager
from kernel.core.flags import FlagManager
from kernel.core.storage import ResourceStore


MEMORY_BODY_PLAINTEXT = "memory-body-plaintext-must-not-enter-sqlite"
SECRET_PLAINTEXT = "memory-secret-plaintext-must-not-enter-sqlite"


async def _module_table(home: Path, *, project_root: Path | None = None) -> KernelModuleTable:
    flags = FlagManager(resource_home=home)
    await flags.initialize()
    config = ConfigManager(resource_home=home)
    await config.startup()
    if project_root is not None:
        setattr(config, "project_root", str(project_root))
    state_dir = home / "state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    return KernelModuleTable(flags=flags, config=config, state_dir=state_dir)


def _write_legacy_config(root: Path, *, skepticism: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.md").write_text(
        "---\n"
        f"skepticism: {skepticism}\n"
        "recency_bias: 4\n"
        "verbosity: 2\n"
        f"credential: {SECRET_PLAINTEXT}\n"
        "---\n\n"
        "Legacy memory declaration notes.\n",
        encoding="utf-8",
    )


def _header(name: str, description: str) -> MemoryHeader:
    return MemoryHeader(
        filename=name,
        name=name,
        description=description,
        category="semantic",
        source="user",
        rel_path=f"semantic/{name}.md",
    )


async def _start_manager(
    home: Path,
    *,
    project_root: Path | None = None,
) -> tuple[KernelModuleTable, MemoryManager]:
    mt = await _module_table(home, project_root=project_root)
    manager = MemoryManager(mt)
    await manager.startup()
    return mt, manager


async def _run() -> dict[str, object]:
    with TemporaryDirectory(prefix="mustang-memory-rs-") as tmp:
        home = Path(tmp)
        memory_root = home / "memory"
        _write_legacy_config(memory_root, skepticism=5)
        store.ensure_directory_tree(memory_root)
        store.write_memory(
            memory_root,
            "semantic",
            _header("alpha", "Global memory entry"),
            MEMORY_BODY_PLAINTEXT,
        )

        mt, manager = await _start_manager(home)
        try:
            memory_startup_from_resource_store = (
                manager.declaration_record is not None
                and manager.declaration_record.declaration["disposition"]["skepticism"] == 5
            )
            legacy_import_once = (
                manager.declaration_import_report is not None
                and manager.declaration_import_report.imported == ("legacy:memory.config",)
            )
            revision_after_import = mt.config.current_revisions()[
                "config.global._.memory.global_declarations"
            ]
            index_text = await manager.get_index_text()
            memory_entries_loaded_from_filesystem = "alpha" in index_text
        finally:
            await manager.shutdown()
            mt.config.close()

        _write_legacy_config(memory_root, skepticism=1)
        mt2, manager2 = await _start_manager(home)
        try:
            legacy_drift_ignored = (
                manager2.declaration_record is not None
                and manager2.declaration_record.declaration["disposition"]["skepticism"] == 5
                and manager2.declaration_import_report is not None
                and manager2.declaration_import_report.drift == ("legacy:memory.config",)
            )
        finally:
            await manager2.shutdown()
            mt2.config.close()

        declarations = MemoryDeclarationStore.open(home)
        try:
            current = declarations.read_global()
            revision_after_add = declarations.write_global(
                {**(current.declaration if current else DEFAULT_DECLARATION), "enabled": False},
                expected_revision=current.revision if current else None,
                actor="probe",
            ).revision
            updated = declarations.write_global(
                {
                    **DEFAULT_DECLARATION,
                    "enabled": False,
                    "index_policy": {
                        **DEFAULT_DECLARATION["index_policy"],
                        "embedding_index_enabled": True,
                    },
                },
                expected_revision=revision_after_add,
                actor="probe",
            )
            revision_after_update = updated.revision
            revision_after_delete = declarations.write_global(
                DEFAULT_DECLARATION,
                expected_revision=revision_after_update,
                actor="probe",
            ).revision
        finally:
            declarations.close()

        mt3, manager3 = await _start_manager(home)
        try:
            memory_reload_sees_resource_store_update = (
                manager3.declaration_record is not None
                and manager3.declaration_record.declaration["enabled"] is True
                and manager3.declaration_record.revision == revision_after_delete
            )
        finally:
            await manager3.shutdown()
            mt3.config.close()

        project_root = home / "project"
        project_memory = project_root / ".mustang" / "memory"
        store.ensure_directory_tree(project_memory)
        store.write_memory(
            project_memory,
            "semantic",
            _header("project-alpha", "Project memory entry"),
            "project memory body",
        )
        mt4, manager4 = await _start_manager(home, project_root=project_root)
        try:
            project_index = await manager4.get_index_text()
            project_scope_still_loaded = "project-alpha" in project_index
        finally:
            await manager4.shutdown()
            mt4.config.close()

        store_handle = ResourceStore.open(home)
        try:
            payload = store_handle.read_tx(
                lambda conn: conn.execute(
                    "SELECT payload_json FROM config_sections "
                    "WHERE file = 'memory' AND section = 'global_declarations'"
                ).fetchone()[0]
            )
            export_path = home / "export.json"
            store_handle.export("json", export_path, dry_run=False)
            exported = orjson.dumps(orjson.loads(export_path.read_bytes())).decode()
        finally:
            store_handle.close()

        memory_data_persisted_as_declaration = (
            MEMORY_BODY_PLAINTEXT in payload or MEMORY_BODY_PLAINTEXT in exported
        )
        plaintext_secret_leaked = SECRET_PLAINTEXT in payload or SECRET_PLAINTEXT in exported

        return {
            "probe": "memory_resource_store",
            "memory_startup_from_resource_store": memory_startup_from_resource_store,
            "legacy_import_once": legacy_import_once,
            "legacy_drift_ignored": legacy_drift_ignored,
            "revision_after_import": revision_after_import,
            "revision_after_add": revision_after_add,
            "revision_after_update": revision_after_update,
            "revision_after_delete": revision_after_delete,
            "memory_reload_sees_resource_store_update": memory_reload_sees_resource_store_update,
            "memory_entries_loaded_from_filesystem": memory_entries_loaded_from_filesystem,
            "project_scope_still_loaded": project_scope_still_loaded,
            "memory_data_persisted_as_declaration": memory_data_persisted_as_declaration,
            "plaintext_secret_leaked": plaintext_secret_leaked,
            "result": "PASS",
        }


def main() -> None:
    result = asyncio.run(_run())
    false_failures = [
        key
        for key, value in result.items()
        if isinstance(value, bool)
        and key not in {"memory_data_persisted_as_declaration", "plaintext_secret_leaked"}
        and not value
    ]
    true_failures = [
        key
        for key in ("memory_data_persisted_as_declaration", "plaintext_secret_leaked")
        if result[key]
    ]
    failures = false_failures + true_failures
    if failures:
        result["result"] = "FAIL"
    for key, value in result.items():
        print(f"{key}={value}")
    if failures:
        raise SystemExit(f"failed checks: {', '.join(failures)}")


if __name__ == "__main__":
    main()
