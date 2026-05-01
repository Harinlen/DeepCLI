"""Agent Control Plane vocabulary tests."""

from __future__ import annotations

from typing import get_type_hints

from kernel.agents import (
    ACP_METHOD_BY_OPERATION,
    MUSTANG_METHOD_BY_OPERATION,
    AgentControlOperation,
    AgentIdentity,
    AgentQueueState,
    AgentRuntimeController,
    AgentRuntimeKind,
    AgentStatus,
    AgentTaskIdentity,
    ControlResult,
    RuntimeTarget,
    StatusSnapshot,
)
from kernel.protocol.acp.namespaces import AcpMethod, MustangMethod


def test_runtime_kinds_cover_planned_backends() -> None:
    assert {item.value for item in AgentRuntimeKind} == {
        "in_process_session_agent",
        "child_kernel",
        "external_acp",
    }


def test_control_operations_map_to_acp_or_mustang_extension() -> None:
    assert ACP_METHOD_BY_OPERATION == {
        AgentControlOperation.create: AcpMethod.SESSION_NEW,
        AgentControlOperation.load: AcpMethod.SESSION_LOAD,
        AgentControlOperation.resume: AcpMethod.SESSION_RESUME,
        AgentControlOperation.prompt: AcpMethod.SESSION_PROMPT,
        AgentControlOperation.cancel: AcpMethod.SESSION_CANCEL,
        AgentControlOperation.close: AcpMethod.SESSION_CLOSE,
    }
    assert MUSTANG_METHOD_BY_OPERATION[AgentControlOperation.delete] == MustangMethod.SESSION_DELETE

    mapped = set(ACP_METHOD_BY_OPERATION) | set(MUSTANG_METHOD_BY_OPERATION)
    assert mapped == set(AgentControlOperation)
    for method in MUSTANG_METHOD_BY_OPERATION.values():
        assert method.startswith("_mustang.agent/")


def test_identity_keeps_mustang_and_external_ids_separate() -> None:
    identity = AgentIdentity(
        agent_id="agent_1",
        runtime_kind=AgentRuntimeKind.external_acp,
        mustang_session_id="mustang-session",
        acp_session_id="acp-session",
        provider_session_id="provider-session",
        acpx_record_id="record-1",
    )

    assert identity.agent_id == "agent_1"
    assert identity.mustang_session_id != identity.acp_session_id
    assert identity.provider_session_id == "provider-session"


def test_status_snapshot_models_queue_and_active_task() -> None:
    identity = AgentIdentity("agent_1", AgentRuntimeKind.in_process_session_agent)
    snapshot = StatusSnapshot(
        identity=identity,
        status=AgentStatus.running,
        queue_state=AgentQueueState.queued,
        active_task_id="task_active",
        queued_task_ids=("task_queued",),
    )

    assert snapshot.status.accepts_new_work
    assert snapshot.active_task_id == "task_active"
    assert snapshot.queued_task_ids == ("task_queued",)


def test_control_result_links_identity_task_and_status() -> None:
    identity = AgentIdentity("agent_1", AgentRuntimeKind.child_kernel)
    task = AgentTaskIdentity(
        task_id="task_1",
        agent_id=identity.agent_id,
        operation=AgentControlOperation.prompt,
    )
    status = StatusSnapshot(identity=identity, status=AgentStatus.idle)

    result = ControlResult(identity=identity, task=task, status=status, output="done")

    assert result.identity is identity
    assert result.task is task
    assert result.status is status
    assert result.output == "done"


def test_runtime_target_describes_backend_without_launching_it() -> None:
    target = RuntimeTarget(
        kind=AgentRuntimeKind.external_acp,
        command=["codex", "--acp", "--stdio"],
        cwd="/tmp/project",
    )

    assert target.kind is AgentRuntimeKind.external_acp
    assert target.command == ["codex", "--acp", "--stdio"]


def test_controller_protocol_exposes_all_control_operations() -> None:
    method_names = {
        name for name in dir(AgentRuntimeController) if not name.startswith("_")
    }
    assert {operation.value for operation in AgentControlOperation} <= method_names
    assert get_type_hints(AgentRuntimeController.create)["return"] is ControlResult
    assert get_type_hints(AgentRuntimeController.status)["return"] is StatusSnapshot
