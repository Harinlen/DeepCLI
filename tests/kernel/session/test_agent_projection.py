from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kernel.agent_hub import AgentHubManager
from kernel.agent_hub.contracts import (
    AgentQueueState,
    AgentStatus,
    default_primary_agent_definition,
)
from kernel.core.protocol.interfaces.contracts.prompt_params import PromptParams
from kernel.core.protocol.interfaces.contracts.text_block import TextBlock
from kernel.agents.mustang.sessions.agent_projection import (
    project_session_agent_status,
    snapshot_to_task_identity,
)
from kernel.agents.mustang.sessions.runtime.state import QueuedTurn, Session, TurnState


class _NoopOrchestrator:
    last_turn_usage = (0, 0)


def _session(
    *,
    in_flight: bool = False,
    queued: int = 0,
) -> Session:
    now = datetime.now(timezone.utc)
    session = Session(
        session_id="s1",
        cwd=Path("/tmp"),
        created_at=now,
        updated_at=now,
        title=None,
        git_branch=None,
        mode_id=None,
        config_options={},
        mcp_servers=[],
        orchestrator=_NoopOrchestrator(),  # type: ignore[arg-type]
    )
    if in_flight:
        task = asyncio.current_task()
        assert task is not None
        session.in_flight_turn = TurnState(
            request_id="req-active",
            client_turn_id=None,
            task=task,
            started_at=now,
            user_message_event_id="ev-user",
            completion_future=asyncio.get_running_loop().create_future(),
        )
    session.queue = deque(
        QueuedTurn(
            request_id=f"req-{idx}",
            client_turn_id=None,
            params=PromptParams(
                session_id="s1",
                prompt=[TextBlock(type="text", text=f"queued {idx}")],
            ),
            queued_at=now,
            response_future=asyncio.get_running_loop().create_future(),
        )
        for idx in range(queued)
    )
    return session


def test_projection_reports_idle_when_agent_has_no_work() -> None:
    snapshot = project_session_agent_status(agent_id="primary", sessions=[_session()])

    assert snapshot.status is AgentStatus.idle
    assert snapshot.queue_state is AgentQueueState.empty
    assert snapshot.queued_task_ids == ()


@pytest.mark.asyncio
async def test_projection_reports_running_and_queued_work() -> None:
    snapshot = project_session_agent_status(
        agent_id="primary",
        sessions=[_session(in_flight=True, queued=2)],
    )

    assert snapshot.status is AgentStatus.running
    assert snapshot.queue_state is AgentQueueState.draining
    assert snapshot.active_task_id == "s1:req-active"
    assert snapshot.queued_task_ids == ("s1:req-0", "s1:req-1")
    assert snapshot_to_task_identity(snapshot).task_id == "s1:req-active"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_projection_prefers_client_turn_id_when_present() -> None:
    session = _session(in_flight=True, queued=1)
    assert session.in_flight_turn is not None
    session.in_flight_turn.client_turn_id = "turn-active"
    session.queue[0].client_turn_id = "turn-queued"

    snapshot = project_session_agent_status(agent_id="primary", sessions=[session])

    assert snapshot.active_task_id == "s1:turn-active"
    assert snapshot.queued_task_ids == ("s1:turn-queued",)


@pytest.mark.asyncio
async def test_manager_stores_projection_without_owning_queue(tmp_path: Path) -> None:
    manager = AgentHubManager(
        [default_primary_agent_definition(home=tmp_path, workspace=tmp_path)]
    )
    snapshot = project_session_agent_status(
        agent_id="primary",
        sessions=[_session(queued=1)],
    )

    record = manager.project_status(snapshot)

    assert record.agent_id == "primary"
    assert record.status is AgentStatus.queued
    assert record.queue_depth == 1
    assert record.active_turn_id is None
    assert manager.get_runtime_record("primary") == record
