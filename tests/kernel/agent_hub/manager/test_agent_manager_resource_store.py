from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

import kernel.agent_hub.manager.manager as manager_module
from kernel.access_router.repository import AccessRouterRepository
from kernel.access_router.schemas import RouteStatus
from kernel.agent_hub.manager.manager import AgentManager
from kernel.agent_hub.manager.schemas import (
    CreateAgentSpec,
    GrantCapability,
    ResourceScope,
)
from kernel.agents.mustang.schedule.store import CronStore
from kernel.agents.mustang.schedule.types import CronTask, Schedule, ScheduleKind
from kernel.core.storage import ResourceStore, tables


def test_create_update_cas_bumps_directory_revision(tmp_path) -> None:
    manager = AgentManager(home=tmp_path)
    manager.startup()
    try:
        created = manager.create(
            CreateAgentSpec(
                agent_id="worker",
                name="Worker",
                workspace=tmp_path / "workspace",
                state_dir=tmp_path / "agents" / "worker",
            ),
            actor_agent_id="primary",
        )
        updated = manager.update(
            "worker",
            name="Worker 2",
            expected_revision=created.revision,
            actor_agent_id="primary",
        )
        snapshot = manager.routing_snapshot()

        assert updated.revision == 2
        assert snapshot.revision == 3  # primary bootstrap + create + update
    finally:
        manager.close()


def test_delete_agent_does_not_delete_workspace(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = tmp_path / "agents" / "worker"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text("{}", encoding="utf-8")
    manager = AgentManager(home=tmp_path)
    manager.startup()
    try:
        created = manager.create(
            CreateAgentSpec(
                agent_id="worker",
                name="Worker",
                workspace=workspace,
                state_dir=state_dir,
            ),
            actor_agent_id="primary",
        )
        result = manager.delete(
            "worker",
            expected_revision=created.revision,
            actor_agent_id="primary",
            confirm=True,
        )

        assert result.deleted is True
        assert result.workspace_deleted is False
        assert result.state_dir_deletion_status == "deleted"
        assert workspace.exists()
        assert not state_dir.exists()
    finally:
        manager.close()


def test_delete_without_confirm_rejected(tmp_path: Path) -> None:
    manager = AgentManager(home=tmp_path)
    manager.startup()
    try:
        created = manager.create(
            CreateAgentSpec(
                agent_id="worker",
                name="Worker",
                workspace=tmp_path / "workspace",
                state_dir=tmp_path / "agents" / "worker",
            ),
            actor_agent_id="primary",
        )
        with pytest.raises(PermissionError):
            manager.delete(
                "worker",
                expected_revision=created.revision,
                actor_agent_id="primary",
                confirm=False,
            )
    finally:
        manager.close()


def test_delete_disables_access_bindings_revokes_grants_and_keeps_agent_bindings_unused(
    tmp_path: Path,
) -> None:
    manager = AgentManager(home=tmp_path)
    manager.startup()
    repo = AccessRouterRepository.open(tmp_path)
    try:
        created = manager.create(
            CreateAgentSpec(
                agent_id="worker",
                name="Worker",
                workspace=tmp_path / "workspace",
                state_dir=tmp_path / "agents" / "worker",
            ),
            actor_agent_id="primary",
        )
        repo.declare_adapter(adapter_id="test", adapter_type="test", config={}, actor="primary")
        repo.set_channel_binding(
            binding_id="test:chan-1",
            adapter_id="test",
            channel_key="chan-1",
            target_agent_id="worker",
            actor="primary",
        )
        grant = manager.grant(
            "worker",
            GrantCapability.GLOBAL_RESOURCE_WRITE,
            ResourceScope.GLOBAL,
            granted_by_agent_id="primary",
        )

        manager.delete(
            "worker",
            expected_revision=created.revision,
            actor_agent_id="primary",
            confirm=True,
        )

        store = ResourceStore.open(tmp_path)
        try:
            binding = store.read_tx(
                lambda conn: conn.execute(
                    sa.select(tables.access_channel_bindings).where(
                        tables.access_channel_bindings.c.binding_id == "test:chan-1"
                    )
                ).fetchone()
            )
            revoked = store.read_tx(
                lambda conn: conn.execute(
                    sa.select(tables.management_grants.c.revoked_at).where(
                        tables.management_grants.c.grant_id == grant.grant_id
                    )
                ).fetchone()
            )
            agent_binding_count = store.read_tx(
                lambda conn: conn.execute(
                    sa.select(sa.func.count()).select_from(tables.agent_bindings)
                ).fetchone()[0]
            )
        finally:
            store.close()
        assert bool(binding["enabled"]) is False
        assert revoked["revoked_at"] is not None
        assert agent_binding_count == 0
    finally:
        repo.close()
        manager.close()


@pytest.mark.anyio
async def test_delete_disables_owned_schedules(tmp_path: Path) -> None:
    manager = AgentManager(home=tmp_path)
    manager.startup()
    schedule_store = CronStore()
    await schedule_store.startup_resource(tmp_path)
    try:
        created = manager.create(
            CreateAgentSpec(
                agent_id="worker",
                name="Worker",
                workspace=tmp_path / "workspace",
                state_dir=tmp_path / "agents" / "worker",
            ),
            actor_agent_id="primary",
        )
        await schedule_store.add(
            CronTask(
                id="sched-worker",
                owner_agent_id="worker",
                schedule=Schedule(kind=ScheduleKind.every, interval_seconds=60),
                prompt="do work",
                created_at=1,
                next_fire_at=2,
            )
        )

        manager.delete(
            "worker",
            expected_revision=created.revision,
            actor_agent_id="primary",
            confirm=True,
        )

        task = await schedule_store.get("sched-worker")
        assert task.status.value == "paused"
        assert task.next_fire_at is None
        assert schedule_store.current_revision("sched-worker") == 2
    finally:
        await schedule_store.shutdown()
        manager.close()


def test_delete_external_state_dir_stays_pending_for_safe_retry(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external_state = tmp_path / "external-state"
    external_state.mkdir()
    manager = AgentManager(home=tmp_path / "resource")
    manager.startup()
    try:
        created = manager.create(
            CreateAgentSpec(
                agent_id="worker",
                name="Worker",
                workspace=workspace,
                state_dir=external_state,
            ),
            actor_agent_id="primary",
        )
        result = manager.delete(
            "worker",
            expected_revision=created.revision,
            actor_agent_id="primary",
            confirm=True,
        )
        record = manager.get("worker")

        assert result.state_dir_deletion_status == "pending"
        assert result.state_dir_cleanup_error is not None
        assert record.state_dir_deletion_status == "pending"
        assert external_state.exists()
        assert workspace.exists()
    finally:
        manager.close()


def test_startup_retries_pending_state_dir_cleanup(tmp_path: Path) -> None:
    state_dir = tmp_path / "agents" / "worker"
    state_dir.mkdir(parents=True)
    manager = AgentManager(home=tmp_path)
    manager.startup()
    try:
        created = manager.create(
            CreateAgentSpec(
                agent_id="worker",
                name="Worker",
                workspace=tmp_path / "workspace",
                state_dir=state_dir,
            ),
            actor_agent_id="primary",
        )
        original_cleanup = manager._cleanup_state_dir_for_deleted_agent
        manager._cleanup_state_dir_for_deleted_agent = lambda **_: "temporary failure"  # type: ignore[method-assign]
        result = manager.delete(
            "worker",
            expected_revision=created.revision,
            actor_agent_id="primary",
            confirm=True,
        )
        manager._cleanup_state_dir_for_deleted_agent = original_cleanup  # type: ignore[method-assign]
        assert result.state_dir_deletion_status == "pending"
    finally:
        manager.close()

    manager = AgentManager(home=tmp_path)
    manager.startup()
    try:
        record = manager.get("worker")
        assert record.state_dir_deletion_status == "deleted"
        assert not state_dir.exists()
    finally:
        manager.close()


def test_grant_guard_denies_ordinary_global_write(tmp_path) -> None:
    manager = AgentManager(home=tmp_path)
    manager.startup()
    try:
        assert not manager.can_manage(
            "worker",
            GrantCapability.GLOBAL_RESOURCE_WRITE,
            ResourceScope.GLOBAL,
        )
        manager.grant(
            "worker",
            GrantCapability.GLOBAL_RESOURCE_WRITE,
            ResourceScope.GLOBAL,
            granted_by_agent_id="primary",
        )
        assert manager.can_manage(
            "worker",
            GrantCapability.GLOBAL_RESOURCE_WRITE,
            ResourceScope.GLOBAL,
        )
    finally:
        manager.close()


def test_primary_bootstrap_is_idempotent(tmp_path) -> None:
    manager = AgentManager(home=tmp_path)
    manager.startup()
    first = manager.get("primary")
    manager.close()

    manager = AgentManager(home=tmp_path)
    manager.startup()
    try:
        second = manager.get("primary")
        assert first is not None
        assert second is not None
        assert second.revision == first.revision
        assert [agent.agent_id for agent in manager.list()] == ["primary"]
    finally:
        manager.close()


def test_health_unhealthy_when_route_stale(tmp_path) -> None:
    manager = AgentManager(home=tmp_path)
    manager.startup()
    try:
        stale = manager.health("primary")
        manager.set_route_status("primary", "registered")
        healthy = manager.health("primary")

        assert stale.healthy is False
        assert stale.reason == "process_not_running"
        assert healthy.healthy is False
    finally:
        manager.close()


def test_health_reports_fresh_stale_and_unavailable_routes(tmp_path: Path) -> None:
    route = RouteStatus(
        agent_id="primary",
        status="registered",
        connection_id="conn-1",
        heartbeat_fresh=True,
        heartbeat_age_seconds=0.1,
    )
    manager = AgentManager(home=tmp_path, route_status_reader=lambda _agent_id: route)
    manager.startup()

    class Proc:
        pid = 4242

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

    manager._processes["primary"] = Proc()
    try:
        fresh = manager.health("primary")
        route = RouteStatus(
            agent_id="primary",
            status="stale",
            connection_id="conn-1",
            heartbeat_fresh=False,
            heartbeat_age_seconds=20.0,
        )
        stale = manager.health("primary")
        route = RouteStatus(
            agent_id="primary",
            status="unavailable",
            heartbeat_fresh=False,
        )
        unavailable = manager.health("primary")

        assert fresh.healthy is True
        assert fresh.runtime_heartbeat_fresh is True
        assert stale.healthy is False
        assert stale.reason == "heartbeat_stale"
        assert stale.route_status == "stale"
        assert unavailable.healthy is False
        assert unavailable.reason == "route_unavailable"
    finally:
        manager.close()


def test_start_spawns_runtime_and_combines_route_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_status = "registered"
    manager = AgentManager(home=tmp_path, route_status_reader=lambda _agent_id: route_status)
    manager.startup()

    class Proc:
        pid = 4242

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def fake_spawn(launch):
        launch.runtime_file.parent.mkdir(parents=True, exist_ok=True)
        launch.runtime_file.write_text('{"registered": true}', encoding="utf-8")
        return Proc()

    monkeypatch.setattr(manager_module, "spawn_runtime", fake_spawn)
    try:
        status = manager.start(
            "primary",
            actor_agent_id="primary",
            router_endpoint="ws://127.0.0.1:8200",
            router_token="token",
        )
        health = manager.health("primary")

        assert status.observed_state == "running"
        assert status.pid == 4242
        assert status.process_running is True
        assert status.route_status == "registered"
        assert health.healthy is True
    finally:
        manager.close()


def test_start_deleted_agent_rejected(tmp_path: Path) -> None:
    manager = AgentManager(home=tmp_path)
    manager.startup()
    try:
        created = manager.create(
            CreateAgentSpec(
                agent_id="worker",
                name="Worker",
                workspace=tmp_path / "workspace",
                state_dir=tmp_path / "agents" / "worker",
            ),
            actor_agent_id="primary",
        )
        manager.delete(
            "worker",
            expected_revision=created.revision,
            actor_agent_id="primary",
            confirm=True,
        )
        with pytest.raises(KeyError):
            manager.start(
                "worker",
                actor_agent_id="primary",
                router_endpoint="ws://127.0.0.1:8200",
                router_token="token",
            )
        stopped = manager.stop("worker", actor_agent_id="primary")
        assert stopped.observed_state == "deleted"
        health = manager.health("worker")
        assert health.healthy is False
        assert health.reason == "deleted"
        assert health.route_status == "deleted"
    finally:
        manager.close()
