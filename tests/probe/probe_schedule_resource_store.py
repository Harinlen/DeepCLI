from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from kernel.agent_hub.manager.manager import AgentManager
from kernel.agent_hub.manager.schemas import CreateAgentSpec
from kernel.agents.mustang.schedule import ScheduleConfig, ScheduleFlags, ScheduleManager
from kernel.agents.mustang.schedule.store import CronStore
from kernel.agents.mustang.schedule.types import CronTask, CronTaskStatus, Schedule, ScheduleKind
from kernel.core.storage import ResourceStore, tables
import sqlalchemy as sa


class _Flags:
    def register(self, _section: str, schema: type[Any]) -> Any:
        return schema()

    def get_section(self, _section: str) -> ScheduleFlags:
        return ScheduleFlags()


class _ConfigSection:
    def get(self) -> ScheduleConfig:
        return ScheduleConfig()


class _Config:
    def get_section(self, **_: Any) -> _ConfigSection:
        return _ConfigSection()


class _SessionManager:
    async def delete_session(self, _session_id: str) -> bool:
        return False


class _ModuleTable:
    def __init__(self, home: Path) -> None:
        self.state_dir = home / "state"
        self.state_dir.mkdir()
        self.flags = _Flags()
        self.config = _Config()
        self.session = _SessionManager()

    def get(self, cls: type[Any]) -> Any:
        from kernel.agents.mustang.sessions import SessionManager

        if cls is SessionManager:
            return self.session
        raise KeyError(cls)


async def _main() -> None:
    with tempfile.TemporaryDirectory(prefix="mustang-schedule-resource-probe-") as raw_home:
        home = Path(raw_home)
        config_dir = home / "config"
        config_dir.mkdir()
        legacy_file = config_dir / "schedules.yaml"
        legacy_file.write_text(
            """
tasks:
  - id: legacy-task
    owner_agent_id: worker
    schedule:
      kind: every
      interval_seconds: 60
    prompt: legacy prompt
    description: Legacy imported task
    next_fire_at: 2000000000
""",
            encoding="utf-8",
        )

        store = CronStore()
        await store.startup_resource(home)
        try:
            legacy = await store.get("legacy-task")
            legacy_imported = legacy is not None and legacy.owner_agent_id == "worker"
        finally:
            await store.shutdown()

        manager_subsystem = ScheduleManager(_ModuleTable(home))  # type: ignore[arg-type]
        await manager_subsystem.startup()
        try:
            startup_task = await manager_subsystem.get_task("legacy-task")
            schedule_manager_startup = startup_task is not None
        finally:
            await manager_subsystem.shutdown()

        legacy_file.write_text(
            """
tasks:
  - id: drift-task
    schedule:
      kind: every
      interval_seconds: 30
    prompt: drift
""",
            encoding="utf-8",
        )
        store = CronStore()
        await store.startup_resource(home)
        try:
            drift_ignored = await store.get("drift-task") is None and bool(
                store.legacy_import_warnings
            )
            task = CronTask(
                id="worker-task",
                owner_agent_id="worker",
                schedule=Schedule(kind=ScheduleKind.every, interval_seconds=120),
                prompt="run worker job",
                created_at=1,
                next_fire_at=2,
            )
            await store.add(task)
            revision_after_add = store.current_revision("worker-task")
            await store.update_status(
                "worker-task",
                CronTaskStatus.paused,
                next_fire_at=None,
            )
            revision_after_update = store.current_revision("worker-task")
        finally:
            await store.shutdown()

        manager = AgentManager(home=home)
        manager.startup()
        store = CronStore()
        await store.startup_resource(home)
        try:
            created = manager.create(
                CreateAgentSpec(
                    agent_id="worker",
                    name="Worker",
                    workspace=home / "workspace",
                    state_dir=home / "agents" / "worker",
                ),
                actor_agent_id="primary",
            )
            await store.update_status(
                "worker-task",
                CronTaskStatus.active,
                next_fire_at=10,
            )
            manager.delete(
                "worker",
                expected_revision=created.revision,
                actor_agent_id="primary",
                confirm=True,
            )
            deleted_task = await store.get("worker-task")
            agent_delete_disabled_schedule = (
                deleted_task is not None
                and deleted_task.status == CronTaskStatus.paused
                and deleted_task.next_fire_at is None
            )
            resource_store = ResourceStore.open(home)
            try:
                row_count = resource_store.read_tx(
                    lambda conn: conn.execute(
                        sa.select(sa.func.count()).select_from(tables.scheduled_tasks)
                    ).fetchone()[0]
                )
            finally:
                resource_store.close()
        finally:
            await store.shutdown()
            manager.close()

        checks = {
            "cron_store_startup_from_resource_store": legacy_imported,
            "schedule_manager_startup_from_resource_store": schedule_manager_startup,
            "legacy_import_once_drift_ignored": drift_ignored,
            "revision_after_add": revision_after_add,
            "revision_after_update": revision_after_update,
            "agent_delete_disabled_schedule": agent_delete_disabled_schedule,
            "scheduled_tasks_rows": row_count,
        }
        for key, value in checks.items():
            print(f"{key}={value}")

        assert checks["cron_store_startup_from_resource_store"] is True
        assert checks["schedule_manager_startup_from_resource_store"] is True
        assert checks["legacy_import_once_drift_ignored"] is True
        assert checks["revision_after_add"] == 1
        assert checks["revision_after_update"] == 2
        assert checks["agent_delete_disabled_schedule"] is True
        assert checks["scheduled_tasks_rows"] >= 2
        print("probe=schedule_resource_store result=PASS")


if __name__ == "__main__":
    asyncio.run(_main())
