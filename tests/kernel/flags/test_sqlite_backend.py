from __future__ import annotations

from kernel.core.flags.manager import FlagManager
from kernel.core.flags.sqlite_backend import FlagSQLiteBackend
from kernel.core.storage import ResourceStore


async def test_flag_write_returns_after_restart_semantics(tmp_path) -> None:
    store = ResourceStore.open(tmp_path)
    try:
        record = FlagSQLiteBackend(store).write(
            section="kernel",
            payload={"memory": False},
            expected_revision=None,
            actor="primary",
        )
    finally:
        store.close()

    assert record.revision == 1

    manager = FlagManager(path=tmp_path / "missing.yaml", resource_home=tmp_path)
    await manager.initialize()
    try:
        assert manager.get_section("kernel").memory is False
    finally:
        manager.close()


async def test_flag_manager_does_not_hot_reload_in_process(tmp_path) -> None:
    store = ResourceStore.open(tmp_path)
    try:
        first = FlagSQLiteBackend(store).write(
            section="kernel",
            payload={"memory": True},
            expected_revision=None,
            actor="primary",
        )
    finally:
        store.close()

    manager = FlagManager(path=tmp_path / "missing.yaml", resource_home=tmp_path)
    await manager.initialize()
    try:
        assert manager.get_section("kernel").memory is True

        store = ResourceStore.open(tmp_path)
        try:
            FlagSQLiteBackend(store).write(
                section="kernel",
                payload={"memory": False},
                expected_revision=first.revision,
                actor="primary",
            )
        finally:
            store.close()

        assert manager.get_section("kernel").memory is True
    finally:
        manager.close()
