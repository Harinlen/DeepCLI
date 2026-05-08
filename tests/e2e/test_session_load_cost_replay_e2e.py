"""E2E coverage for session/load transcript replay plus /cost after switch."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from kernel.session.events import (
    AgentMessageEvent,
    ConversationMessageEvent,
    TurnCompletedEvent,
    serialize_event,
)
from probe.client import AgentChunk, ProbeClient, ToolCallEvent, UsageEvent, UserChunk

_TEST_TIMEOUT = 30.0


def _run(coro: Any) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=_TEST_TIMEOUT)

    return asyncio.run(_guarded())


def _sessions_db() -> Path:
    return (
        Path(tempfile.gettempdir()) / "mustang-e2e-kernel" / ".deepcli" / "sessions" / "sessions.db"
    )


def _insert_event(db: sqlite3.Connection, session_id: str, timestamp: datetime, event: Any) -> None:
    db.execute(
        "INSERT INTO session_events (session_id, timestamp, context) VALUES (?, ?, ?)",
        (session_id, timestamp.isoformat(), serialize_event(event).strip()),
    )


def _seed_conversation_only_history(session_id: str, cwd: str) -> None:
    """Seed the exact shape that regressed: conversation rows but no UI agent rows."""
    db_path = _sessions_db()
    assert db_path.exists(), f"sessions.db not found at {db_path}"
    now = datetime.now(UTC)
    base = {
        "session_id": session_id,
        "cwd": cwd,
    }
    with sqlite3.connect(db_path) as db:
        _insert_event(
            db,
            session_id,
            now,
            ConversationMessageEvent(
                event_id="ev_e2e_user",
                timestamp=now,
                message={"role": "user", "content": [{"type": "text", "text": "loaded question"}]},
                **base,
            ),
        )
        _insert_event(
            db,
            session_id,
            now + timedelta(milliseconds=1),
            ConversationMessageEvent(
                event_id="ev_e2e_assistant",
                timestamp=now + timedelta(milliseconds=1),
                message={
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "loaded answer"},
                        {
                            "type": "tool_use",
                            "id": "tool-e2e",
                            "name": "Bash",
                            "input": {"command": "pwd"},
                        },
                    ],
                },
                **base,
            ),
        )
        _insert_event(
            db,
            session_id,
            now + timedelta(milliseconds=2),
            TurnCompletedEvent(
                event_id="ev_e2e_turn_done",
                timestamp=now + timedelta(milliseconds=2),
                stop_reason="stop",
                duration_ms=3000,
                input_tokens=14_443,
                output_tokens=125,
                **base,
            ),
        )
        db.execute(
            """
            UPDATE sessions
            SET total_input_tokens = ?, total_output_tokens = ?, modified = ?
            WHERE session_id = ?
            """,
            (14_443, 125, (now + timedelta(milliseconds=2)).isoformat(), session_id),
        )
        db.commit()


def _seed_mixed_incomplete_ui_history(session_id: str, cwd: str) -> None:
    """Seed a legacy shape with incomplete UI rows plus full conversation rows."""
    db_path = _sessions_db()
    assert db_path.exists(), f"sessions.db not found at {db_path}"
    now = datetime.now(UTC)
    base = {
        "session_id": session_id,
        "cwd": cwd,
    }
    with sqlite3.connect(db_path) as db:
        _insert_event(
            db,
            session_id,
            now,
            ConversationMessageEvent(
                event_id="ev_e2e_mixed_user",
                timestamp=now,
                message={"role": "user", "content": [{"type": "text", "text": "mixed question"}]},
                **base,
            ),
        )
        _insert_event(
            db,
            session_id,
            now + timedelta(milliseconds=1),
            ConversationMessageEvent(
                event_id="ev_e2e_mixed_assistant",
                timestamp=now + timedelta(milliseconds=1),
                message={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "mixed answer"}],
                },
                **base,
            ),
        )
        _insert_event(
            db,
            session_id,
            now + timedelta(milliseconds=2),
            AgentMessageEvent(
                event_id="ev_e2e_empty_ui_agent",
                timestamp=now + timedelta(milliseconds=2),
                content=[],
                **base,
            ),
        )
        db.execute(
            """
            UPDATE sessions
            SET total_input_tokens = ?, total_output_tokens = ?, modified = ?
            WHERE session_id = ?
            """,
            (14_443, 125, (now + timedelta(milliseconds=2)).isoformat(), session_id),
        )
        db.commit()


def test_session_load_replays_conversation_history_and_cost(kernel: tuple[int, str]) -> None:
    port, token = kernel

    async def _run_test() -> None:
        async with ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT) as client:
            await client.initialize()
            session_id = await client.new_session()
            _seed_conversation_only_history(session_id, str(Path.cwd()))

            history = await client.load_session(session_id)
            assert any(
                isinstance(event, UserChunk) and event.text == "loaded question"
                for event in history
            )
            assert any(
                isinstance(event, AgentChunk) and event.text == "loaded answer" for event in history
            )
            assert any(
                isinstance(event, ToolCallEvent) and event.tool_call_id == "tool-e2e"
                for event in history
            )
            replay_usage = [event for event in history if isinstance(event, UsageEvent)]
            assert replay_usage, "session/load should replay a usage_update context snapshot"
            assert replay_usage[-1].used == 14_568

            usage = await client._request(
                "_mustang.agent/session/get_usage",
                {"sessionId": session_id},
            )
            assert usage["sessionId"] == session_id
            assert usage["tokens"]["input"] == 14_443
            assert usage["tokens"]["output"] == 125
            assert usage["tokens"]["total"] == 14_568
            assert usage["context"]["totalTokens"] == 14_568
            assert replay_usage[-1].used == usage["context"]["totalTokens"]

    _run(_run_test())


def test_session_resume_replays_usage_snapshot(kernel: tuple[int, str]) -> None:
    port, token = kernel

    async def _run_test() -> None:
        async with ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT) as client:
            await client.initialize()
            session_id = await client.new_session()
            _seed_conversation_only_history(session_id, str(Path.cwd()))

            await client._request(
                "session/resume", {"sessionId": session_id, "cwd": str(Path.cwd())}
            )
            resume_events = await client.drain_events()
            replay_usage = [event for event in resume_events if isinstance(event, UsageEvent)]
            assert replay_usage, "session/resume should emit a usage_update context snapshot"

            usage = await client._request(
                "_mustang.agent/session/get_usage",
                {"sessionId": session_id},
            )
            assert replay_usage[-1].used == usage["context"]["totalTokens"] == 14_568

    _run(_run_test())


def test_session_load_recovers_mixed_incomplete_ui_history(kernel: tuple[int, str]) -> None:
    port, token = kernel

    async def _run_test() -> None:
        async with ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT) as client:
            await client.initialize()
            session_id = await client.new_session()
            _seed_mixed_incomplete_ui_history(session_id, str(Path.cwd()))

            history = await client.load_session(session_id)
            assert any(
                isinstance(event, UserChunk) and event.text == "mixed question" for event in history
            )
            assert any(
                isinstance(event, AgentChunk) and event.text == "mixed answer" for event in history
            )

            usage = await client._request(
                "_mustang.agent/session/get_usage",
                {"sessionId": session_id},
            )
            assert usage["tokens"]["total"] == 14_568

    _run(_run_test())
