from __future__ import annotations

from pathlib import Path

import pytest

from kernel.agent_hub import (
    AgentDefinitionsConfig,
    AgentHub,
    AgentHubManager,
    AgentHubRouter,
    ResourceRevisionTracker,
)
from kernel.agent_hub.contracts import (
    AgentBindings,
    AgentDefinition,
    AgentPolicy,
    AgentRole,
    AgentRuntimeDeclaration,
    AgentRuntimeKind,
    CallerIdentity,
    CallerIdentityKind,
    ManagementCapability,
    PlatformBinding,
    RouterFrame,
    RouterFrameKind,
    RouterTarget,
    default_primary_agent_definition,
)
from kernel.core.config import ConfigManager


def _caller() -> CallerIdentity:
    return CallerIdentity(
        kind=CallerIdentityKind.DURABLE_AGENT,
        subject_id="agent:primary",
        agent_id="primary",
    )


def _session_definition(tmp_path: Path, agent_id: str = "peer") -> AgentDefinition:
    return AgentDefinition(
        id=agent_id,
        name="Peer",
        role=AgentRole.SESSION,
        workspace=str(tmp_path),
        state_dir=str(tmp_path / ".mustang" / "agents" / agent_id),
        runtime=AgentRuntimeDeclaration(
            kind=AgentRuntimeKind.in_process_session_agent
        ),
        policy=AgentPolicy(),
        bindings=AgentBindings(
            platforms=(
                PlatformBinding(
                    adapter_id="discord-main",
                    platform="discord",
                    account_id="account-1",
                ),
            )
        ),
    )


async def test_agent_definitions_config_can_be_owned_by_config_manager(
    tmp_path: Path,
) -> None:
    config = ConfigManager(global_dir=tmp_path / "global", project_dir=tmp_path / "project")
    await config.startup()

    section = config.bind_section(
        file="agents",
        section="definitions",
        schema=AgentDefinitionsConfig,
    )

    assert section.get().agents == ()


def test_manager_definition_and_runtime_record_boundaries(tmp_path: Path) -> None:
    manager = AgentHubManager(
        [default_primary_agent_definition(home=tmp_path, workspace=tmp_path)]
    )
    peer = _session_definition(tmp_path)

    manager.create_definition(
        peer,
        caller=_caller(),
        capability=ManagementCapability.AGENT_CREATE,
    )
    runtime = manager.get_runtime_record("peer")

    assert manager.get_definition("peer") == peer
    assert runtime is not None
    assert runtime.process_id is None
    assert "process_id" not in peer.model_dump()

    assert manager.delete_definition(
        "peer",
        caller=_caller(),
        capability=ManagementCapability.AGENT_DELETE,
    )
    assert manager.get_runtime_record("peer") is None


def test_manager_materializes_binding_plan_and_routing_snapshot(tmp_path: Path) -> None:
    manager = AgentHubManager(
        [
            default_primary_agent_definition(home=tmp_path, workspace=tmp_path),
            _session_definition(tmp_path),
        ]
    )

    binding_plan = manager.binding_plan(revision=4)
    snapshot = manager.routing_snapshot(revision=5)

    assert binding_plan.entries[0].target_agent_id == "peer"
    assert snapshot.revision == 5
    assert any(entry.native_default for entry in snapshot.entries)


def test_router_uses_snapshot_without_manager_reference(tmp_path: Path) -> None:
    manager = AgentHubManager(
        [default_primary_agent_definition(home=tmp_path, workspace=tmp_path)]
    )
    router = AgentHubRouter()
    router.update_snapshot(manager.routing_snapshot(revision=1))

    frame = RouterFrame(
        frame_id="frame-1",
        kind=RouterFrameKind.USER_MESSAGE,
        source="access:native",
        target=RouterTarget(),
        caller=CallerIdentity(
            kind=CallerIdentityKind.ACCESS,
            subject_id="cli:test",
        ),
        payload={"text": "hello"},
    )

    assert router.resolve_target(frame) == "primary"
    routed = router.route_message(frame)
    assert routed is not None
    assert routed.target_agent_id == "primary"
    assert not hasattr(router, "manager")


def test_router_rejects_targets_not_in_durable_snapshot(tmp_path: Path) -> None:
    manager = AgentHubManager(
        [default_primary_agent_definition(home=tmp_path, workspace=tmp_path)]
    )
    router = AgentHubRouter()
    router.update_snapshot(manager.routing_snapshot(revision=1))

    frame = RouterFrame(
        frame_id="frame-ephemeral",
        kind=RouterFrameKind.AGENT_MESSAGE,
        source="agent:primary",
        target=RouterTarget(agent_id="a_ephemeral_child"),
        caller=CallerIdentity(
            kind=CallerIdentityKind.DURABLE_AGENT,
            subject_id="agent:primary",
            agent_id="primary",
        ),
        payload={"text": "hello"},
    )

    assert router.resolve_target(frame) is None
    assert router.route_message(frame) is None


def test_resource_revision_tracker_validates_capability() -> None:
    monitor = ResourceRevisionTracker()
    caller = _caller()

    event = monitor.write(
        "skills.global",
        caller=caller,
        capability=ManagementCapability.GLOBAL_RESOURCE_WRITE,
        expected_revision=0,
    )

    assert event.revision == 1
    assert monitor.current_revisions()["skills.global"] == 1
    with pytest.raises(PermissionError):
        monitor.write(
            "skills.global",
            caller=CallerIdentity(
                kind=CallerIdentityKind.ACCESS,
                subject_id="cli:test",
            ),
            capability=ManagementCapability.GLOBAL_RESOURCE_WRITE,
        )


def test_agent_hub_readiness_does_not_import_fastapi() -> None:
    hub = AgentHub()

    assert hub.readiness()["ready"] is True
    assert "fastapi" not in type(hub).__module__
