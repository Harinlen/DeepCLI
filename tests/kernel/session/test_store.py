"""Unit tests for SessionStore (SQLite backend).

All tests use an in-memory SQLite database so they are fast, isolated,
and leave no files on disk.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from kernel.session.events import (
    SessionCreatedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
    UserMessageEvent,
)
from kernel.session.events import KERNEL_VERSION
from kernel.session.models import ConversationRecord, TokenUsageUpdate
from kernel.session.store import SessionStore

# Mark every async test in this module to run under anyio (asyncio backend).
pytestmark = pytest.mark.anyio
UTC = timezone.utc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path: Path) -> SessionStore:  # type: ignore[misc]
    """SessionStore backed by a real on-disk SQLite (tmp_path is per-test)."""
    s = SessionStore(tmp_path / "sessions")
    await s.open()
    yield s  # type: ignore[misc]
    await s.close()


def _sid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


def _make_created_event(session_id: str, cwd: str = "/work") -> SessionCreatedEvent:
    return SessionCreatedEvent(
        event_id="ev_" + uuid.uuid4().hex,
        parent_id=None,
        timestamp=_now(),
        session_id=session_id,
        agent_depth=0,
        kernel_version=KERNEL_VERSION,
        cwd=cwd,
        git_branch=None,
        mcp_servers=[],
    )


def _make_user_event(
    session_id: str,
    parent_id: str | None = None,
    text: str = "hello",
    client_turn_id: str | None = None,
) -> UserMessageEvent:
    return UserMessageEvent(
        event_id="ev_" + uuid.uuid4().hex,
        parent_id=parent_id,
        timestamp=_now(),
        session_id=session_id,
        agent_depth=0,
        kernel_version=KERNEL_VERSION,
        cwd="/work",
        git_branch=None,
        content=[{"type": "text", "text": text}],
        request_id=None,
        client_turn_id=client_turn_id,
    )


def _make_turn_completed(
    session_id: str,
    parent_id: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    client_turn_id: str | None = None,
) -> TurnCompletedEvent:
    return TurnCompletedEvent(
        event_id="ev_" + uuid.uuid4().hex,
        parent_id=parent_id,
        timestamp=_now(),
        session_id=session_id,
        agent_depth=0,
        kernel_version=KERNEL_VERSION,
        cwd="/work",
        git_branch=None,
        stop_reason="end_turn",
        client_turn_id=client_turn_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _make_turn_started(
    session_id: str,
    parent_id: str | None = None,
    client_turn_id: str | None = None,
) -> TurnStartedEvent:
    return TurnStartedEvent(
        event_id="ev_" + uuid.uuid4().hex,
        parent_id=parent_id,
        timestamp=_now(),
        session_id=session_id,
        agent_depth=0,
        kernel_version=KERNEL_VERSION,
        cwd="/work",
        git_branch=None,
        client_turn_id=client_turn_id,
    )


def _make_record(session_id: str, cwd: str = "/work") -> ConversationRecord:
    return ConversationRecord(session_id=session_id, cwd=cwd, title=None)


# ---------------------------------------------------------------------------
# open / schema
# ---------------------------------------------------------------------------


async def test_open_creates_db_file(tmp_path: Path) -> None:
    s = SessionStore(tmp_path / "sessions")
    await s.open()
    assert (tmp_path / "sessions" / "sessions.db").exists()
    await s.close()


async def test_open_is_idempotent(tmp_path: Path) -> None:
    """Opening twice on the same DB should not raise."""
    s1 = SessionStore(tmp_path / "sessions")
    await s1.open()
    await s1.close()

    s2 = SessionStore(tmp_path / "sessions")
    await s2.open()
    await s2.close()


# ---------------------------------------------------------------------------
# create_session_with_events
# ---------------------------------------------------------------------------


async def test_create_session_with_events_stores_record(store: SessionStore) -> None:
    sid = _sid()
    record = _make_record(sid)
    ev = _make_created_event(sid)

    await store.create_session_with_events(record, [ev])

    fetched = await store.get_session(sid)
    assert fetched is not None
    assert fetched.session_id == sid
    assert fetched.cwd == "/work"
    assert fetched.title is None


async def test_create_session_with_events_stores_events(store: SessionStore) -> None:
    sid = _sid()
    ev = _make_created_event(sid)
    await store.create_session_with_events(_make_record(sid), [ev])

    events = await store.read_events(sid)
    assert len(events) == 1
    assert isinstance(events[0], SessionCreatedEvent)
    assert events[0].session_id == sid


async def test_create_session_multiple_initial_events(store: SessionStore) -> None:
    sid = _sid()
    ev1 = _make_created_event(sid)
    ev2 = _make_user_event(sid, parent_id=ev1.event_id)
    await store.create_session_with_events(_make_record(sid), [ev1, ev2])

    events = await store.read_events(sid)
    assert len(events) == 2


# ---------------------------------------------------------------------------
# append_event
# ---------------------------------------------------------------------------


async def test_append_event_without_tokens(store: SessionStore) -> None:
    sid = _sid()
    await store.create_session_with_events(_make_record(sid), [_make_created_event(sid)])

    user_ev = _make_user_event(sid, text="second message")
    await store.append_event(sid, user_ev)

    events = await store.read_events(sid)
    assert len(events) == 2
    assert isinstance(events[1], UserMessageEvent)


async def test_append_event_with_tokens_updates_counters(store: SessionStore) -> None:
    sid = _sid()
    await store.create_session_with_events(_make_record(sid), [_make_created_event(sid)])

    tc = _make_turn_completed(sid, input_tokens=100, output_tokens=50)
    tokens = TokenUsageUpdate(input_tokens_delta=100, output_tokens_delta=50)
    await store.append_event(sid, tc, tokens=tokens)

    record = await store.get_session(sid)
    assert record is not None
    assert record.total_input_tokens == 100
    assert record.total_output_tokens == 50


async def test_token_deltas_accumulate_across_turns(store: SessionStore) -> None:
    sid = _sid()
    await store.create_session_with_events(_make_record(sid), [_make_created_event(sid)])

    for in_t, out_t in [(100, 50), (200, 80), (150, 30)]:
        tc = _make_turn_completed(sid, input_tokens=in_t, output_tokens=out_t)
        await store.append_event(sid, tc, tokens=TokenUsageUpdate(in_t, out_t))

    record = await store.get_session(sid)
    assert record is not None
    assert record.total_input_tokens == 450  # 100 + 200 + 150
    assert record.total_output_tokens == 160  # 50 + 80 + 30


async def test_append_event_zero_delta_no_update(store: SessionStore) -> None:
    """A TokenUsageUpdate with both deltas = 0 should not touch the DB row."""
    sid = _sid()
    await store.create_session_with_events(_make_record(sid), [_make_created_event(sid)])

    tc = _make_turn_completed(sid)
    await store.append_event(sid, tc, tokens=TokenUsageUpdate(0, 0))

    record = await store.get_session(sid)
    assert record is not None
    assert record.total_input_tokens == 0
    assert record.total_output_tokens == 0


async def test_find_turn_by_client_turn_id_returns_completed_state(
    store: SessionStore,
) -> None:
    sid = _sid()
    client_turn_id = str(uuid.uuid4())
    created = _make_created_event(sid)
    await store.create_session_with_events(_make_record(sid), [created])
    user_event = _make_user_event(
        sid,
        parent_id=created.event_id,
        client_turn_id=client_turn_id,
    )
    started_event = _make_turn_started(
        sid,
        parent_id=user_event.event_id,
        client_turn_id=client_turn_id,
    )
    completed_event = _make_turn_completed(
        sid,
        parent_id=started_event.event_id,
        client_turn_id=client_turn_id,
    )
    await store.append_event(sid, user_event)
    await store.append_event(sid, started_event)
    await store.append_event(sid, completed_event)

    result = await store.find_turn_by_client_turn_id(sid, client_turn_id)

    assert result is not None
    assert result.state == "completed"
    assert result.stop_reason == "end_turn"


async def test_find_turn_by_client_turn_id_returns_incomplete_state(
    store: SessionStore,
) -> None:
    sid = _sid()
    client_turn_id = str(uuid.uuid4())
    await store.create_session_with_events(_make_record(sid), [_make_created_event(sid)])
    await store.append_event(
        sid,
        _make_user_event(sid, client_turn_id=client_turn_id),
    )

    result = await store.find_turn_by_client_turn_id(sid, client_turn_id)

    assert result is not None
    assert result.state == "incomplete"


# ---------------------------------------------------------------------------
# update_title
# ---------------------------------------------------------------------------


async def test_update_title(store: SessionStore) -> None:
    sid = _sid()
    await store.create_session_with_events(_make_record(sid), [_make_created_event(sid)])

    await store.update_title(sid, "My great session")

    record = await store.get_session(sid)
    assert record is not None
    assert record.title == "My great session"


async def test_update_title_overwrites(store: SessionStore) -> None:
    sid = _sid()
    record = _make_record(sid)
    record.title = "Initial"
    await store.create_session_with_events(record, [_make_created_event(sid)])

    await store.update_title(sid, "Updated")

    fetched = await store.get_session(sid)
    assert fetched is not None
    assert fetched.title == "Updated"


async def test_update_title_sets_title_source(store: SessionStore) -> None:
    sid = _sid()
    await store.create_session_with_events(_make_record(sid), [_make_created_event(sid)])

    await store.update_title(sid, "User title", title_source="user")

    fetched = await store.get_session(sid)
    assert fetched is not None
    assert fetched.title_source == "user"


# ---------------------------------------------------------------------------
# read_events — ordering
# ---------------------------------------------------------------------------


async def test_read_events_returns_timestamp_order(store: SessionStore) -> None:
    """Events must come back oldest-first regardless of insertion order."""
    import asyncio

    sid = _sid()
    ev1 = _make_created_event(sid)
    await store.create_session_with_events(_make_record(sid), [ev1])

    # Insert two more events with a tiny sleep to get different timestamps.
    ev2 = _make_user_event(sid, text="first prompt")
    await asyncio.sleep(0.01)
    ev3 = _make_user_event(sid, text="second prompt")
    await store.append_event(sid, ev2)
    await store.append_event(sid, ev3)

    events = await store.read_events(sid)
    assert len(events) == 3
    timestamps = [e.timestamp for e in events]
    assert timestamps == sorted(timestamps)


async def test_read_events_empty_for_unknown_session(store: SessionStore) -> None:
    events = await store.read_events("no-such-session")
    assert events == []


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


async def test_list_sessions_sorted_by_modified_desc(store: SessionStore) -> None:
    import asyncio

    sids = [_sid() for _ in range(3)]
    for sid in sids:
        await store.create_session_with_events(_make_record(sid), [_make_created_event(sid)])
        await asyncio.sleep(0.01)  # ensure distinct modified timestamps

    records = await store.list_sessions()
    assert len(records) >= 3
    session_ids_in_result = [r.session_id for r in records]
    for sid in sids:
        assert sid in session_ids_in_result

    # Verify descending order.
    modifieds = [r.modified for r in records[:3]]
    assert modifieds == sorted(modifieds, reverse=True)


async def test_list_sessions_empty(store: SessionStore) -> None:
    records = await store.list_sessions()
    assert records == []


async def test_list_sessions_filters_archived(store: SessionStore) -> None:
    active, archived = _sid(), _sid()
    await store.create_session_with_events(_make_record(active), [_make_created_event(active)])
    await store.create_session_with_events(_make_record(archived), [_make_created_event(archived)])
    await store.archive_session(archived, "2026-04-28T00:00:00+00:00")

    default_ids = [record.session_id for record in await store.list_sessions()]
    include_ids = [record.session_id for record in await store.list_sessions(include_archived=True)]
    archived_ids = [record.session_id for record in await store.list_sessions(archived_only=True)]

    assert active in default_ids
    assert archived not in default_ids
    assert active in include_ids
    assert archived in include_ids
    assert archived_ids == [archived]


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------


async def test_get_session_returns_none_for_unknown(store: SessionStore) -> None:
    record = await store.get_session("nonexistent")
    assert record is None


async def test_get_session_returns_record(store: SessionStore) -> None:
    sid = _sid()
    await store.create_session_with_events(_make_record(sid), [_make_created_event(sid)])

    record = await store.get_session(sid)
    assert record is not None
    assert record.session_id == sid


# ---------------------------------------------------------------------------
# delete_session
# ---------------------------------------------------------------------------


async def test_delete_session_removes_record_and_events(store: SessionStore) -> None:
    sid = _sid()
    ev = _make_created_event(sid)
    await store.create_session_with_events(_make_record(sid), [ev])
    await store.append_event(sid, _make_user_event(sid))

    deleted = await store.delete_session(sid)

    assert deleted is True
    assert await store.get_session(sid) is None
    assert await store.read_events(sid) == []


async def test_delete_session_only_removes_target(store: SessionStore) -> None:
    """Deleting one session must not affect other sessions."""
    sid1, sid2 = _sid(), _sid()
    await store.create_session_with_events(_make_record(sid1), [_make_created_event(sid1)])
    await store.create_session_with_events(_make_record(sid2), [_make_created_event(sid2)])

    deleted = await store.delete_session(sid1)

    assert deleted is True
    assert await store.get_session(sid1) is None
    assert await store.get_session(sid2) is not None
    assert len(await store.read_events(sid2)) == 1


async def test_delete_session_returns_false_for_missing(store: SessionStore) -> None:
    """Deleting a missing session should be a no-op, not a successful delete."""
    assert await store.delete_session(_sid()) is False


# ---------------------------------------------------------------------------
# tool-result spillover
# ---------------------------------------------------------------------------


async def test_write_and_read_spilled(store: SessionStore) -> None:
    sid = _sid()
    content = "a" * 20_000
    rel_path, result_hash = store.write_spilled(sid, content)

    assert rel_path == f"{sid}/tool-results/{result_hash}.txt"
    assert store.read_spilled(sid, result_hash) == content
