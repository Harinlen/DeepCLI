from __future__ import annotations

from pathlib import Path

import pytest

import kernel.agent_hub.manager.manager as manager_module
from kernel.agent_hub.manager.manager import AgentManager
from kernel.agent_hub.manager.schemas import (
    CreateAgentSpec,
    GrantCapability,
    ResourceScope,
)


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
    manager = AgentManager(home=tmp_path)
    manager.startup()
    try:
        created = manager.create(
            CreateAgentSpec(
                agent_id="worker",
                name="Worker",
                workspace=workspace,
                state_dir=tmp_path / "agents" / "worker",
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
        assert workspace.exists()
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
    finally:
        manager.close()
