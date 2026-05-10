from __future__ import annotations

import pytest
from pydantic import ValidationError

from kernel.agent_hub.contracts.control_plane import AgentRuntimeKind, AgentStatus
from kernel.agent_hub.contracts.schema import (
    AgentBindingSpec,
    AgentDefinition,
    AgentPolicySpec,
    AgentResourceSpec,
    AgentRole,
    AgentRuntimeSpec,
    BindingPlan,
    BindingPlanEntry,
    CallerIdentity,
    CallerKind,
    ManagementCapability,
    PlatformBindingSpec,
    RegistrationToken,
    ReplySink,
    RouterFrame,
    RoutingContext,
    RoutingSnapshot,
    RuntimeRecord,
)


def test_process_backed_runtime_requires_command() -> None:
    with pytest.raises(ValidationError, match="command is required"):
        AgentRuntimeSpec(kind=AgentRuntimeKind.child_kernel)

    spec = AgentRuntimeSpec(
        kind=AgentRuntimeKind.child_kernel,
        command=("python", "-m", "kernel"),
        env={"HOME": "/tmp/home"},
        endpoint="ws://127.0.0.1:1/runtime",
        profile="primary",
    )

    assert spec.command == ("python", "-m", "kernel")
    assert spec.env["HOME"] == "/tmp/home"


def test_agent_definition_rejects_extra_runtime_state() -> None:
    payload = {
        "id": "primary",
        "name": "Primary",
        "role": "primary",
        "workspace": "/workspace",
        "state_dir": "/state",
        "runtime": {"kind": "in_process_session_agent"},
        "process_id": 123,
    }

    with pytest.raises(ValidationError):
        AgentDefinition.model_validate(payload)


def test_agent_definition_defaults_and_alias_dump() -> None:
    definition = AgentDefinition(
        id="primary",
        name="Primary",
        role=AgentRole.primary,
        workspace="/workspace",
        state_dir="/state",
        runtime=AgentRuntimeSpec(kind=AgentRuntimeKind.in_process_session_agent),
        policy=AgentPolicySpec(management_capabilities=("agent.status",)),
        bindings=AgentBindingSpec(
            native_default=True,
            platforms=(
                PlatformBindingSpec(
                    adapter_id="discord-main",
                    platform="discord",
                    channel_id="chan-1",
                ),
            ),
        ),
        resources=AgentResourceSpec(model_profile="sonnet"),
        metadata={"owner": "test"},
    )

    dumped = definition.model_dump(by_alias=True)

    assert dumped["id"] == "primary"
    assert definition.agent_id == "primary"
    assert definition.bindings.native_default is True
    assert definition.bindings.platforms[0].enabled is True
    assert definition.resources.memory_scopes == ("global", "workspace", "agent")


def test_management_capability_requires_durable_agent_caller() -> None:
    access = CallerIdentity(kind=CallerKind.access_client, subject_id="cli:test")

    with pytest.raises(ValidationError, match="durable agent caller"):
        ManagementCapability(caller=access, capability="agent.status", agent_id="primary")

    durable = CallerIdentity(
        kind=CallerKind.durable_agent,
        subject_id="agent:manager",
        agent_id="manager",
        metadata={"scope": "test"},
    )
    capability = ManagementCapability(
        caller=durable,
        capability="agent.status",
        agent_id="primary",
    )

    assert capability.caller.agent_id == "manager"


def test_runtime_record_router_frame_and_binding_snapshots() -> None:
    record = RuntimeRecord(
        agent_id="primary",
        runtime_kind=AgentRuntimeKind.in_process_session_agent,
        process_id=42,
        websocket_endpoint="ws://127.0.0.1:10/runtime",
        status=AgentStatus.running,
        restart_count=2,
        queue_depth=3,
        active_turn_id="turn-1",
    )
    caller = CallerIdentity(
        kind=CallerKind.access_client,
        subject_id="cli:test",
        connection_id="conn-1",
    )
    sink = ReplySink(sink_id="conn-1", kind="native_ws")
    frame = RouterFrame(
        source="access:native",
        target="primary",
        caller=caller,
        routing=RoutingContext(native_default=True),
        session_id="session-1",
        message_kind="prompt",
        payload={"text": "hello"},
        correlation_id="corr-1",
        reply_sink=sink,
    )
    binding = BindingPlanEntry(
        adapter_id="discord-main",
        platform="discord",
        target_agent_id="primary",
        channel_id="chan-1",
    )
    plan = BindingPlan(revision=7, entries=(binding,))
    snapshot = RoutingSnapshot(
        revision=7,
        default_agent_id="primary",
        agent_ids=("primary",),
        platform_bindings=plan.entries,
    )

    assert record.status is AgentStatus.running
    assert frame.reply_sink == sink
    assert frame.routing.native_default is True
    assert plan.entries[0].target_agent_id == "primary"
    assert snapshot.platform_bindings[0].channel_id == "chan-1"


def test_registration_token_defaults_to_supervisor_issuer() -> None:
    token = RegistrationToken(
        token_id="tok-1",
        agent_id="primary",
        issued_at="2026-05-01T00:00:00Z",
        expires_at="2026-05-01T00:01:00Z",
    )

    assert token.issuer == "supervisor"
