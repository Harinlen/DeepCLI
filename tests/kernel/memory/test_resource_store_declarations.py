"""ResourceStore-backed global memory declaration tests."""

from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from kernel.agents.mustang.memory import MemoryManager, store
from kernel.agents.mustang.memory.declarations import (
    DEFAULT_DECLARATION,
    MemoryDeclarationStore,
)
from kernel.agents.mustang.memory.types import MemoryHeader
from kernel.agents.mustang.module_table import KernelModuleTable
from kernel.core.config import ConfigManager
from kernel.core.flags import FlagManager
from kernel.core.storage import ResourceStore


async def _module_table(home: Path) -> KernelModuleTable:
    flags = FlagManager(resource_home=home)
    await flags.initialize()
    config = ConfigManager(resource_home=home)
    await config.startup()
    state_dir = home / "state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    return KernelModuleTable(flags=flags, config=config, state_dir=state_dir)


def _write_legacy_config(root: Path, *, skepticism: int, plaintext: str = "") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.md").write_text(
        "---\n"
        f"skepticism: {skepticism}\n"
        "recency_bias: 4\n"
        "verbosity: 2\n"
        f"credential: {plaintext}\n"
        "---\n\n"
        "Memory declaration notes that must not become memory entries.\n",
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


@pytest.mark.anyio
async def test_memory_declarations_startup_from_resource_store(tmp_path: Path) -> None:
    declarations = MemoryDeclarationStore.open(tmp_path)
    try:
        declarations.write_global(
            {**DEFAULT_DECLARATION, "enabled": False},
            expected_revision=None,
            actor="test",
        )
    finally:
        declarations.close()

    mt = await _module_table(tmp_path)
    manager = MemoryManager(mt)
    await manager.startup()
    try:
        assert manager.declaration_record is not None
        assert manager.declaration_record.declaration["enabled"] is False
        assert mt.config.current_revisions()["config.global._.memory.global_declarations"] == 1
    finally:
        await manager.shutdown()
        mt.config.close()


@pytest.mark.anyio
async def test_legacy_memory_config_import_once_and_drift_ignored(tmp_path: Path) -> None:
    _write_legacy_config(tmp_path / "memory", skepticism=5)

    first = await _module_table(tmp_path)
    first_manager = MemoryManager(first)
    await first_manager.startup()
    await first_manager.shutdown()
    first.config.close()

    _write_legacy_config(tmp_path / "memory", skepticism=1)
    second = await _module_table(tmp_path)
    second_manager = MemoryManager(second)
    await second_manager.startup()
    try:
        assert second_manager.declaration_record is not None
        assert second_manager.declaration_record.declaration["disposition"]["skepticism"] == 5
        assert second_manager.declaration_import_report is not None
        assert second_manager.declaration_import_report.drift == ("legacy:memory.config",)
    finally:
        await second_manager.shutdown()
        second.config.close()


def test_memory_declaration_revision_bumps_on_add_update_delete(tmp_path: Path) -> None:
    declarations = MemoryDeclarationStore.open(tmp_path)
    try:
        one = declarations.write_global(DEFAULT_DECLARATION, expected_revision=None, actor="test")
        two_payload = {**one.declaration, "enabled": False}
        two = declarations.write_global(two_payload, expected_revision=one.revision, actor="test")
        three_payload = {
            **two.declaration,
            "index_policy": {**two.declaration["index_policy"], "embedding_index_enabled": True},
        }
        three = declarations.write_global(
            three_payload,
            expected_revision=two.revision,
            actor="test",
        )
        four = declarations.write_global(DEFAULT_DECLARATION, expected_revision=three.revision, actor="test")

        assert (one.revision, two.revision, three.revision, four.revision) == (1, 2, 3, 4)
    finally:
        declarations.close()


@pytest.mark.anyio
async def test_memory_reload_sees_resource_store_declaration_update(tmp_path: Path) -> None:
    declarations = MemoryDeclarationStore.open(tmp_path)
    try:
        one = declarations.write_global(DEFAULT_DECLARATION, expected_revision=None, actor="test")
        declarations.write_global(
            {**one.declaration, "enabled": False},
            expected_revision=one.revision,
            actor="test",
        )
    finally:
        declarations.close()

    mt = await _module_table(tmp_path)
    manager = MemoryManager(mt)
    await manager.startup()
    try:
        assert manager.declaration_record is not None
        assert manager.declaration_record.declaration["enabled"] is False
        assert manager.declaration_record.revision == 2
    finally:
        await manager.shutdown()
        mt.config.close()


@pytest.mark.anyio
async def test_memory_entries_and_runtime_state_not_persisted_as_declarations(tmp_path: Path) -> None:
    body_plaintext = "memory-entry-body-should-not-be-in-resource-store"
    secret_plaintext = "memory-secret-should-not-be-in-resource-store"
    _write_legacy_config(tmp_path / "memory", skepticism=4, plaintext=secret_plaintext)
    store.ensure_directory_tree(tmp_path / "memory")
    store.write_memory(
        tmp_path / "memory",
        "semantic",
        _header("alpha", "Alpha declaration-safe description"),
        body_plaintext,
    )

    mt = await _module_table(tmp_path)
    manager = MemoryManager(mt)
    await manager.startup()
    try:
        assert await manager.get_index_text()
        store_handle = ResourceStore.open(tmp_path)
        try:
            payload = store_handle.read_tx(
                lambda conn: conn.execute(
                    "SELECT payload_json FROM config_sections "
                    "WHERE file = 'memory' AND section = 'global_declarations'"
                ).fetchone()[0]
            )
            export_path = tmp_path / "export.json"
            store_handle.export("json", export_path, dry_run=False)
            exported = orjson.dumps(orjson.loads(export_path.read_bytes())).decode()
        finally:
            store_handle.close()

        assert body_plaintext not in payload
        assert secret_plaintext not in payload
        assert body_plaintext not in exported
        assert secret_plaintext not in exported
    finally:
        await manager.shutdown()
        mt.config.close()
