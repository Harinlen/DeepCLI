from __future__ import annotations

import pytest
from pydantic import BaseModel

from kernel.core.config import ConfigManager
from kernel.core.config.sqlite_backend import ConfigSQLiteBackend
from kernel.core.storage import ResourceStore, RevisionConflict


class ToolsConfig(BaseModel):
    bash_timeout: int = 120


def test_config_write_read_with_expected_revision(tmp_path) -> None:
    store = ResourceStore.open(tmp_path)
    try:
        backend = ConfigSQLiteBackend(store)
        created = backend.write(
            file="config",
            section="tools",
            payload={"bash_timeout": 60},
            expected_revision=None,
            actor="primary",
        )
        updated = backend.write(
            file="config",
            section="tools",
            payload={"bash_timeout": 90},
            expected_revision=created.revision,
            actor="primary",
        )

        assert updated.revision == 2
        assert backend.read(file="config", section="tools") == updated
        assert store.current_revisions("config.") == {"config.global._.config.tools": 2}
    finally:
        store.close()


def test_config_conflict_reports_current_revision(tmp_path) -> None:
    store = ResourceStore.open(tmp_path)
    try:
        backend = ConfigSQLiteBackend(store)
        current = backend.write(
            file="config",
            section="tools",
            payload={"bash_timeout": 60},
            expected_revision=None,
            actor="primary",
        )

        with pytest.raises(RevisionConflict) as exc_info:
            backend.write(
                file="config",
                section="tools",
                payload={"bash_timeout": 90},
                expected_revision=99,
                actor="primary",
            )

        assert exc_info.value.current_revision == current.revision
        assert exc_info.value.current_hash == current.payload_hash
    finally:
        store.close()


async def test_config_manager_refreshes_sqlite_sections(tmp_path) -> None:
    store = ResourceStore.open(tmp_path)
    try:
        backend = ConfigSQLiteBackend(store)
        first = backend.write(
            file="config",
            section="tools",
            payload={"bash_timeout": 60},
            expected_revision=None,
            actor="primary",
        )
    finally:
        store.close()

    manager = ConfigManager(global_dir=tmp_path / "yaml", resource_home=tmp_path)
    await manager.startup()
    try:
        section = manager.get_section(file="config", section="tools", schema=ToolsConfig)
        assert section.get().bash_timeout == 60

        store = ResourceStore.open(tmp_path)
        try:
            ConfigSQLiteBackend(store).write(
                file="config",
                section="tools",
                payload={"bash_timeout": 90},
                expected_revision=first.revision,
                actor="primary",
            )
        finally:
            store.close()

        manager.refresh_from_resource_store()
        assert section.get().bash_timeout == 90
        assert manager.current_revisions() == {"config.global._.config.tools": 2}
    finally:
        manager.close()
