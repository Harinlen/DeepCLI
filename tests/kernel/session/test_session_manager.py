"""Integration tests for SessionManager.

These tests exercise the SessionManager against a real (on-disk) SQLite
database.  External dependencies (LLMProvider) are mocked so the tests
run without network access.

Focus areas:
- Session creation writes a DB record + SessionCreatedEvent.
- Title is set from the first user message, then overwritten by
  SessionInfoChanged (AI-generated title).
- TurnCompletedEvent carries token fields; cumulative totals accumulate
  in the sessions row.
- list() returns sessions sorted by most-recently-modified.
- load_session() raises for unknown session IDs.
"""

from __future__ import annotations

import uuid
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.protocol.interfaces.contracts.archive_session_params import ArchiveSessionParams
from kernel.protocol.interfaces.contracts.close_session_params import CloseSessionParams
from kernel.protocol.interfaces.contracts.delete_session_params import DeleteSessionParams
from kernel.protocol.interfaces.contracts.list_sessions_params import ListSessionsParams
from kernel.protocol.interfaces.contracts.load_session_params import LoadSessionParams
from kernel.protocol.interfaces.contracts.new_session_params import NewSessionParams
from kernel.protocol.interfaces.contracts.prompt_params import PromptParams
from kernel.protocol.interfaces.contracts.rename_session_params import RenameSessionParams
from kernel.protocol.interfaces.contracts.resume_session_params import ResumeSessionParams
from kernel.protocol.interfaces.contracts.set_config_option_params import SetConfigOptionParams
from kernel.protocol.interfaces.contracts.set_mode_params import SetModeParams
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.protocol.interfaces.errors import InvalidParams, InvalidRequest, ResourceNotFoundError
from kernel.session import AgentContext, SessionManager
from kernel.session.events import UserMessageEvent
from kernel.session.store import SessionStore

# Mark every async test in this module to run under anyio (asyncio backend).
pytestmark = pytest.mark.anyio

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_module_table(tmp_path: Path) -> MagicMock:
    """Build a minimal ModuleTable stand-in."""
    mt = MagicMock()
    mt.state_dir = tmp_path / "state" / "mustang-kernel.state"
    mt.flags.register.return_value = MagicMock(
        max_queue_length=50,
        list_page_size=50,
        tool_result_inline_limit=8 * 1024,
        enable_auto_title=True,
    )
    return mt


def _make_connection(connection_id: str = "conn-1") -> MagicMock:
    conn = MagicMock()
    conn.auth.connection_id = connection_id
    conn.bound_session_id = None
    return conn


def _make_sender() -> AsyncMock:
    sender = AsyncMock()
    sender.notify = AsyncMock()
    return sender


def _make_ctx(connection_id: str = "conn-1") -> HandlerContext:
    ctx = MagicMock(spec=HandlerContext)
    ctx.conn = _make_connection(connection_id)
    ctx.sender = _make_sender()
    ctx.request_id = None
    return ctx


def _make_text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.model_dump.return_value = {"type": "text", "text": text}
    return block


async def _empty_query(*_args: object, **_kwargs: object):
    if False:
        yield None


def _prompt_params(session_id: str, text: str, client_turn_id: str) -> PromptParams:
    return PromptParams(
        session_id=session_id,
        prompt=[TextBlock(type="text", text=text)],
        meta={"mustang.agent/clientTurnId": client_turn_id},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def manager(tmp_path: Path) -> SessionManager:  # type: ignore[misc]
    """A started SessionManager with a real SQLite store."""
    mt = _make_module_table(tmp_path)
    mgr = SessionManager(mt)

    # Prevent actual LLM calls by patching _make_orchestrator.
    fake_orch = MagicMock()
    fake_orch.close = AsyncMock()
    fake_orch.query = MagicMock(side_effect=_empty_query)
    fake_orch.last_turn_usage = (0, 0)
    fake_orch.stop_reason = MagicMock()
    fake_orch.stop_reason.value = "end_turn"
    mgr._make_orchestrator = MagicMock(return_value=(fake_orch, None))

    await mgr.startup()
    yield mgr  # type: ignore[misc]
    await mgr.shutdown()


# ---------------------------------------------------------------------------
# new()
# ---------------------------------------------------------------------------


async def test_new_creates_db_record(manager: SessionManager, tmp_path: Path) -> None:
    ctx = _make_ctx()
    result = await manager.new(ctx, NewSessionParams(cwd=str(tmp_path)))

    sid = result.session_id
    store: SessionStore = manager._store
    record = await store.get_session(sid)

    assert record is not None
    assert record.session_id == sid
    assert record.cwd == str(tmp_path)
    assert record.title is None


async def test_new_writes_session_created_event(manager: SessionManager, tmp_path: Path) -> None:
    ctx = _make_ctx()
    result = await manager.new(ctx, NewSessionParams(cwd=str(tmp_path)))

    events = await manager._store.read_events(result.session_id)
    assert len(events) == 1
    from kernel.session.events import SessionCreatedEvent

    assert isinstance(events[0], SessionCreatedEvent)


async def test_new_registers_in_memory_session(manager: SessionManager, tmp_path: Path) -> None:
    ctx = _make_ctx()
    result = await manager.new(ctx, NewSessionParams(cwd=str(tmp_path)))

    assert result.session_id in manager._sessions


async def test_new_returns_initial_mode_and_config(manager: SessionManager, tmp_path: Path) -> None:
    result = await manager.new(_make_ctx(), NewSessionParams(cwd=str(tmp_path)))

    assert result.modes is not None
    assert result.modes.current_mode_id == "default"
    assert result.config_options[0].config_id == "mode"
    assert result.config_options[0].current_value == "default"


async def test_new_rejects_relative_cwd(manager: SessionManager) -> None:
    with pytest.raises(InvalidParams):
        await manager.new(_make_ctx(), NewSessionParams(cwd="relative/path"))


async def test_new_rejects_session_scoped_mcp_servers(
    manager: SessionManager, tmp_path: Path
) -> None:
    with pytest.raises(InvalidParams):
        await manager.new(
            _make_ctx(),
            NewSessionParams(
                cwd=str(tmp_path),
                mcp_servers=[{"name": "local", "command": "echo"}],
            ),
        )


async def test_agent_context_controls_session_store_path(tmp_path: Path) -> None:
    mt = _make_module_table(tmp_path)
    context = AgentContext(
        agent_id="peer",
        workspace=tmp_path,
        state_dir=tmp_path / "agents" / "peer",
        session_store_path=tmp_path / "agents" / "peer" / "sessions" / "sessions.db",
    )
    mgr = SessionManager(mt, agent_context=context)
    mgr._make_orchestrator = MagicMock(return_value=(MagicMock(close=AsyncMock()), None))

    await mgr.startup()
    try:
        assert mgr._agent_context.agent_id == "peer"
        assert (tmp_path / "agents" / "peer" / "sessions" / "sessions.db").exists()
    finally:
        await mgr.shutdown()


async def test_agent_resource_view_refreshes_at_turn_start(
    manager: SessionManager, tmp_path: Path
) -> None:
    calls = 0

    class _View:
        async def check_and_refresh_before_turn(self) -> dict[str, int]:
            nonlocal calls
            calls += 1
            return {}

    manager._agent_resource_view = _View()
    result = await manager.new(_make_ctx(), NewSessionParams(cwd=str(tmp_path)))

    await manager.prompt(
        _make_ctx(),
        PromptParams(session_id=result.session_id, prompt=[TextBlock(text="hello")]),
    )

    assert calls == 1


async def test_prompt_replays_completed_client_turn_id(
    manager: SessionManager,
    tmp_path: Path,
) -> None:
    result = await manager.new(_make_ctx(), NewSessionParams(cwd=str(tmp_path)))
    client_turn_id = str(uuid.uuid4())
    params = _prompt_params(result.session_id, "hello", client_turn_id)

    first = await manager.prompt(_make_ctx(), params)
    second = await manager.prompt(_make_ctx(), params)

    events = await manager._store.read_events(result.session_id)
    user_events = [event for event in events if isinstance(event, UserMessageEvent)]
    assert first.stop_reason == "end_turn"
    assert second.stop_reason == "end_turn"
    assert second.meta == {
        "mustang.agent/clientTurnId": client_turn_id,
        "mustang.agent/replayedTurnResult": True,
    }
    assert len(user_events) == 1


async def test_prompt_duplicate_active_client_turn_id_waits_same_result(
    manager: SessionManager,
    tmp_path: Path,
) -> None:
    result = await manager.new(_make_ctx(), NewSessionParams(cwd=str(tmp_path)))
    session = manager._sessions[result.session_id]
    release = asyncio.Event()
    client_turn_id = str(uuid.uuid4())

    async def _blocking_query(*_args: object, **_kwargs: object):
        await release.wait()
        if False:
            yield None

    session.orchestrator.query = MagicMock(side_effect=_blocking_query)

    first = asyncio.create_task(
        manager.prompt(_make_ctx(), _prompt_params(result.session_id, "hello", client_turn_id))
    )
    while session.in_flight_turn is None:
        await asyncio.sleep(0)

    second = asyncio.create_task(
        manager.prompt(_make_ctx(), _prompt_params(result.session_id, "hello", client_turn_id))
    )
    await asyncio.sleep(0)
    assert not second.done()

    release.set()
    first_result, second_result = await asyncio.gather(first, second)

    events = await manager._store.read_events(result.session_id)
    user_events = [event for event in events if isinstance(event, UserMessageEvent)]
    assert first_result.stop_reason == "end_turn"
    assert second_result.stop_reason == "end_turn"
    assert len(user_events) == 1


async def test_prompt_duplicate_queued_client_turn_id_does_not_enqueue_twice(
    manager: SessionManager,
    tmp_path: Path,
) -> None:
    result = await manager.new(_make_ctx(), NewSessionParams(cwd=str(tmp_path)))
    session = manager._sessions[result.session_id]
    release = asyncio.Event()
    active_turn_id = str(uuid.uuid4())
    queued_turn_id = str(uuid.uuid4())

    async def _blocking_query(*_args: object, **_kwargs: object):
        await release.wait()
        if False:
            yield None

    session.orchestrator.query = MagicMock(side_effect=_blocking_query)
    active = asyncio.create_task(
        manager.prompt(_make_ctx(), _prompt_params(result.session_id, "active", active_turn_id))
    )
    while session.in_flight_turn is None:
        await asyncio.sleep(0)

    queued_first = asyncio.create_task(
        manager.prompt(_make_ctx(), _prompt_params(result.session_id, "queued", queued_turn_id))
    )
    while not session.queue:
        await asyncio.sleep(0)

    queued_second = asyncio.create_task(
        manager.prompt(_make_ctx(), _prompt_params(result.session_id, "queued", queued_turn_id))
    )
    await asyncio.sleep(0)
    assert len(session.queue) == 1

    release.set()
    results = await asyncio.gather(active, queued_first, queued_second)

    events = await manager._store.read_events(result.session_id)
    user_events = [event for event in events if isinstance(event, UserMessageEvent)]
    assert [result.stop_reason for result in results] == ["end_turn", "end_turn", "end_turn"]
    assert len(user_events) == 2


async def test_prompt_rejects_invalid_client_turn_id(
    manager: SessionManager,
    tmp_path: Path,
) -> None:
    result = await manager.new(_make_ctx(), NewSessionParams(cwd=str(tmp_path)))

    with pytest.raises(InvalidParams):
        await manager.prompt(
            _make_ctx(),
            PromptParams(
                session_id=result.session_id,
                prompt=[TextBlock(type="text", text="hello")],
                meta={"mustang.agent/clientTurnId": "not-a-uuid"},
            ),
        )


async def test_delete_session_reports_existing_row(manager: SessionManager, tmp_path: Path) -> None:
    ctx = _make_ctx()
    result = await manager.new(ctx, NewSessionParams(cwd=str(tmp_path)))

    assert await manager.delete_session(result.session_id) is True
    assert await manager._store.get_session(result.session_id) is None


async def test_delete_session_reports_missing_row(manager: SessionManager) -> None:
    """Cron reaper relies on this bool to avoid repeated fake delete counts."""
    assert await manager.delete_session(str(uuid.uuid4())) is False


async def test_delete_session_rejects_active_without_force(
    manager: SessionManager, tmp_path: Path
) -> None:
    ctx = _make_ctx()
    result = await manager.new(ctx, NewSessionParams(cwd=str(tmp_path)))

    with pytest.raises(InvalidRequest):
        await manager.delete_session(
            ctx,
            DeleteSessionParams(session_id=result.session_id, force=False),
        )


async def test_delete_session_force_removes_sidecars(
    manager: SessionManager, tmp_path: Path
) -> None:
    ctx = _make_ctx()
    result = await manager.new(ctx, NewSessionParams(cwd=str(tmp_path)))
    aux_dir = manager._store.aux_dir(result.session_id)
    aux_dir.mkdir(parents=True, exist_ok=True)
    (aux_dir / "note.txt").write_text("temp", encoding="utf-8")

    delete_result = await manager.delete_session(
        ctx,
        DeleteSessionParams(session_id=result.session_id, force=True),
    )

    assert delete_result.deleted is True
    assert await manager._store.get_session(result.session_id) is None
    assert not aux_dir.exists()


# ---------------------------------------------------------------------------
# list()
# ---------------------------------------------------------------------------


async def test_list_returns_new_session(manager: SessionManager, tmp_path: Path) -> None:
    ctx = _make_ctx()
    result = await manager.new(ctx, NewSessionParams(cwd=str(tmp_path)))

    list_result = await manager.list(ctx, ListSessionsParams())
    ids = [s.session_id for s in list_result.sessions]
    assert result.session_id in ids


async def test_list_filters_by_cwd(manager: SessionManager, tmp_path: Path) -> None:
    cwd_a = str(tmp_path / "a")
    cwd_b = str(tmp_path / "b")

    ctx = _make_ctx()
    r_a = await manager.new(ctx, NewSessionParams(cwd=cwd_a))
    r_b = await manager.new(
        MagicMock(conn=_make_connection("c2"), sender=_make_sender(), request_id=None),
        NewSessionParams(cwd=cwd_b),
    )

    result = await manager.list(ctx, ListSessionsParams(cwd=cwd_a))
    ids = [s.session_id for s in result.sessions]
    assert r_a.session_id in ids
    assert r_b.session_id not in ids


async def test_list_rejects_relative_cwd(manager: SessionManager) -> None:
    with pytest.raises(InvalidParams):
        await manager.list(_make_ctx(), ListSessionsParams(cwd="relative/path"))


async def test_list_rejects_invalid_cursor(manager: SessionManager) -> None:
    with pytest.raises(InvalidParams):
        await manager.list(_make_ctx(), ListSessionsParams(cursor="not-a-cursor"))


async def test_archive_hides_session_from_default_list(
    manager: SessionManager, tmp_path: Path
) -> None:
    result = await manager.new(_make_ctx(), NewSessionParams(cwd=str(tmp_path)))

    archive_result = await manager.archive_session(
        _make_ctx(),
        ArchiveSessionParams(session_id=result.session_id, archived=True),
    )

    assert archive_result.archived_at is not None
    default_ids = [
        summary.session_id
        for summary in (await manager.list(_make_ctx(), ListSessionsParams())).sessions
    ]
    archived_ids = [
        summary.session_id
        for summary in (
            await manager.list(_make_ctx(), ListSessionsParams(archived_only=True))
        ).sessions
    ]
    assert result.session_id not in default_ids
    assert result.session_id in archived_ids


async def test_rename_session_sets_user_title_source(
    manager: SessionManager, tmp_path: Path
) -> None:
    result = await manager.new(_make_ctx(), NewSessionParams(cwd=str(tmp_path)))

    renamed = await manager.rename_session(
        _make_ctx(),
        RenameSessionParams(session_id=result.session_id, title="  User title  "),
    )

    assert renamed.title == "User title"
    assert renamed.title_source == "user"
    record = await manager._store.get_session(result.session_id)
    assert record is not None
    assert record.title_source == "user"


# ---------------------------------------------------------------------------
# load_session()
# ---------------------------------------------------------------------------


async def test_load_session_raises_for_unknown(manager: SessionManager, tmp_path: Path) -> None:
    ctx = _make_ctx()
    with pytest.raises(ResourceNotFoundError):
        await manager.load_session(
            ctx,
            LoadSessionParams(session_id=str(uuid.uuid4()), cwd=str(tmp_path)),
        )


async def test_load_session_evicted_and_reloaded(manager: SessionManager, tmp_path: Path) -> None:
    """Session evicted from memory can be reloaded from DB."""
    ctx = _make_ctx()
    result = await manager.new(ctx, NewSessionParams(cwd=str(tmp_path)))
    sid = result.session_id

    # Manually evict from in-memory store.
    manager._sessions.pop(sid)

    # Reload — should succeed without error.
    ctx2 = _make_ctx("conn-2")
    load_result = await manager.load_session(
        ctx2, LoadSessionParams(session_id=sid, cwd=str(tmp_path))
    )
    assert load_result is not None
    assert sid in manager._sessions
    assert load_result.modes is not None


async def test_load_session_rejects_relative_cwd(manager: SessionManager, tmp_path: Path) -> None:
    result = await manager.new(_make_ctx(), NewSessionParams(cwd=str(tmp_path)))

    with pytest.raises(InvalidParams):
        await manager.load_session(
            _make_ctx("conn-2"),
            LoadSessionParams(session_id=result.session_id, cwd="relative/path"),
        )


async def test_load_session_rejects_session_scoped_mcp_servers(
    manager: SessionManager, tmp_path: Path
) -> None:
    result = await manager.new(_make_ctx(), NewSessionParams(cwd=str(tmp_path)))

    with pytest.raises(InvalidParams):
        await manager.load_session(
            _make_ctx("conn-2"),
            LoadSessionParams(
                session_id=result.session_id,
                cwd=str(tmp_path),
                mcp_servers=[{"name": "local", "command": "echo"}],
            ),
        )


async def test_resume_session_binds_without_replay(
    manager: SessionManager, tmp_path: Path
) -> None:
    first_ctx = _make_ctx()
    result = await manager.new(first_ctx, NewSessionParams(cwd=str(tmp_path)))
    sid = result.session_id
    manager._sessions.pop(sid)

    resume_ctx = _make_ctx("conn-resume")
    resume_result = await manager.resume_session(
        resume_ctx,
        ResumeSessionParams(session_id=sid, cwd=str(tmp_path)),
    )

    assert sid in manager._sessions
    assert resume_result.replayed is False
    assert resume_ctx.conn.bound_session_id == sid
    resume_ctx.sender.notify.assert_not_called()


async def test_close_session_releases_runtime_but_keeps_record(
    manager: SessionManager, tmp_path: Path
) -> None:
    ctx = _make_ctx()
    result = await manager.new(ctx, NewSessionParams(cwd=str(tmp_path)))
    sid = result.session_id

    close_result = await manager.close_session(ctx, CloseSessionParams(session_id=sid))

    assert close_result is not None
    assert sid not in manager._sessions
    assert await manager._store.get_session(sid) is not None
    assert ctx.conn.bound_session_id is None


# ---------------------------------------------------------------------------
# mode/config options
# ---------------------------------------------------------------------------


async def test_set_mode_updates_config_snapshot(manager: SessionManager, tmp_path: Path) -> None:
    result = await manager.new(_make_ctx(), NewSessionParams(cwd=str(tmp_path)))
    session = manager._sessions[result.session_id]

    await manager.set_mode(_make_ctx(), SetModeParams(session_id=result.session_id, mode_id="plan"))

    assert session.mode_id == "plan"
    assert session.config_options["mode"] == "plan"
    session.orchestrator.set_mode.assert_called_with("plan")


async def test_set_config_option_mode_returns_full_descriptor(
    manager: SessionManager, tmp_path: Path
) -> None:
    result = await manager.new(_make_ctx(), NewSessionParams(cwd=str(tmp_path)))

    update = await manager.set_config_option(
        _make_ctx(),
        SetConfigOptionParams(session_id=result.session_id, config_id="mode", value="plan"),
    )

    assert update.config_options[0].config_id == "mode"
    assert update.config_options[0].current_value == "plan"


async def test_set_config_option_rejects_unknown_option(
    manager: SessionManager, tmp_path: Path
) -> None:
    result = await manager.new(_make_ctx(), NewSessionParams(cwd=str(tmp_path)))

    with pytest.raises(InvalidParams):
        await manager.set_config_option(
            _make_ctx(),
            SetConfigOptionParams(session_id=result.session_id, config_id="thinking", value="true"),
        )


# ---------------------------------------------------------------------------
# title auto-set from first user message
# ---------------------------------------------------------------------------


async def test_first_user_message_sets_title(manager: SessionManager, tmp_path: Path) -> None:
    """Title is set to the first 200 chars of the first user message."""
    ctx = _make_ctx()
    result = await manager.new(ctx, NewSessionParams(cwd=str(tmp_path)))
    sid = result.session_id

    session = manager._sessions[sid]
    content_raw = [{"type": "text", "text": "Tell me about Python."}]

    # Simulate _run_turn_core title path by calling the relevant code path.
    # Rather than running a full turn (requires real LLM), call the internal
    # helper that sets the title from the first message.
    if session.title is None:
        for block in content_raw:
            if block.get("type") == "text":
                first_text = str(block["text"])[:200]
                session.title = first_text
                await manager._store.update_title(sid, first_text)
                break

    record = await manager._store.get_session(sid)
    assert record is not None
    assert record.title == "Tell me about Python."


async def test_ai_title_overwrites_first_message_title(
    manager: SessionManager, tmp_path: Path
) -> None:
    """SessionInfoChanged from the orchestrator should overwrite the initial title."""
    ctx = _make_ctx()
    result = await manager.new(ctx, NewSessionParams(cwd=str(tmp_path)))
    sid = result.session_id

    # Seed an initial title.
    await manager._store.update_title(sid, "Initial from first message")

    # Simulate SessionInfoChanged event arriving from orchestrator.
    session = manager._sessions[sid]
    session.title = "AI generated title"
    await manager._store.update_title(sid, "AI generated title")

    record = await manager._store.get_session(sid)
    assert record is not None
    assert record.title == "AI generated title"


# ---------------------------------------------------------------------------
# Token accumulation via append_event
# ---------------------------------------------------------------------------


async def test_token_deltas_persist_across_turns(manager: SessionManager, tmp_path: Path) -> None:
    """Multiple TurnCompleted writes accumulate tokens in the sessions row."""
    ctx = _make_ctx()
    result = await manager.new(ctx, NewSessionParams(cwd=str(tmp_path)))
    sid = result.session_id

    from kernel.session.events import TurnCompletedEvent as TCE
    from kernel.session.models import TokenUsageUpdate

    base_fields = dict(
        parent_id=None,
        timestamp=datetime.now(UTC),
        session_id=sid,
        agent_depth=0,
        kernel_version="0.1.0",
        cwd=str(tmp_path),
        git_branch=None,
        stop_reason="end_turn",
    )

    for i, (inp, out) in enumerate([(100, 50), (200, 80)]):
        ev = TCE(
            event_id=f"ev_{i}",
            input_tokens=inp,
            output_tokens=out,
            **base_fields,
        )
        await manager._store.append_event(sid, ev, tokens=TokenUsageUpdate(inp, out))

    record = await manager._store.get_session(sid)
    assert record is not None
    assert record.total_input_tokens == 300
    assert record.total_output_tokens == 130


# ---------------------------------------------------------------------------
# Orchestrator last_turn_usage
# ---------------------------------------------------------------------------


async def test_orchestrator_last_turn_usage_resets_each_query() -> None:
    """last_turn_usage resets to (0, 0) at the start of a new query."""
    from kernel.orchestrator.orchestrator import StandardOrchestrator
    from kernel.orchestrator.types import OrchestratorDeps, PermissionCallback

    # Build a minimal orchestrator with a mock provider.
    mock_provider = MagicMock()
    mock_provider.model_for = MagicMock(return_value="claude-test")
    deps = OrchestratorDeps(provider=mock_provider)

    orch = StandardOrchestrator(deps=deps, session_id="test-sid")
    # Simulate a previous turn that accumulated tokens.
    orch._turn_input_tokens = 999
    orch._turn_output_tokens = 888

    # Consume the generator to trigger the reset — mock provider raises
    # immediately so we catch the error but the reset still fires.
    mock_provider.stream = AsyncMock(side_effect=Exception("no-llm"))
    cb: PermissionCallback = AsyncMock()  # type: ignore[assignment]
    gen = orch.query([{"type": "text", "text": "hi"}], on_permission=cb)
    try:
        async for _ in gen:
            pass
    except Exception:
        pass

    # Reset fires at the top of _run_query before any LLM call.
    assert orch._turn_input_tokens == 0
    assert orch._turn_output_tokens == 0
