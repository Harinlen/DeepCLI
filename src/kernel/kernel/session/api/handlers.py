"""ACP entry points implemented by SessionManager.

Each public method (``new``, ``load``, ``list``, ``prompt``, ``set_mode``,
``set_config_option``, ``cancel``) maps directly to one ACP request kind.
The mixin owns request → session lookup, queueing, and the side-effects
that must be persisted as ``SessionEvent`` rows or broadcast to clients.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

from kernel.protocol.acp.schemas.updates import ConfigOptionUpdate, CurrentModeUpdate
from kernel.protocol.acp.schemas.updates import SessionInfoUpdate, SessionUpdateNotification, UsageUpdate
from kernel.protocol.interfaces.contracts.archive_session_params import ArchiveSessionParams
from kernel.protocol.interfaces.contracts.archive_session_result import ArchiveSessionResult
from kernel.protocol.interfaces.contracts.cancel_params import CancelParams
from kernel.protocol.interfaces.contracts.close_session_params import CloseSessionParams
from kernel.protocol.interfaces.contracts.close_session_result import CloseSessionResult
from kernel.protocol.interfaces.contracts.get_usage_params import GetUsageParams
from kernel.protocol.interfaces.contracts.get_usage_result import (
    ContextUsageSection,
    ContextUsageSummary,
    EnvironmentUsageSummary,
    GetUsageResult,
    HistoryUsageSummary,
    MemoryUsageSummary,
    TokenUsageSummary,
)
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.protocol.interfaces.contracts.list_sessions_params import ListSessionsParams
from kernel.protocol.interfaces.contracts.list_sessions_result import (
    ListSessionsResult,
    SessionSummary,
)
from kernel.protocol.interfaces.contracts.load_session_params import LoadSessionParams
from kernel.protocol.interfaces.contracts.load_session_result import LoadSessionResult
from kernel.protocol.interfaces.contracts.new_session_params import NewSessionParams
from kernel.protocol.interfaces.contracts.new_session_result import NewSessionResult
from kernel.protocol.interfaces.contracts.prompt_params import PromptParams
from kernel.protocol.interfaces.contracts.prompt_result import PromptResult
from kernel.protocol.interfaces.contracts.rename_session_params import RenameSessionParams
from kernel.protocol.interfaces.contracts.rename_session_result import RenameSessionResult
from kernel.protocol.interfaces.contracts.resume_session_params import ResumeSessionParams
from kernel.protocol.interfaces.contracts.resume_session_result import ResumeSessionResult
from kernel.protocol.interfaces.contracts.set_config_option_params import (
    SetConfigOptionParams,
)
from kernel.protocol.interfaces.contracts.set_config_option_result import SetConfigOptionResult
from kernel.protocol.interfaces.errors import InternalError, InvalidParams, ResourceNotFoundError
from kernel.protocol.interfaces.contracts.set_mode_params import SetModeParams
from kernel.protocol.interfaces.contracts.set_mode_result import SetModeResult
from kernel.session._shared.base import _SessionMixinBase
from kernel.session.events import (
    ConfigOptionChangedEvent,
    ModeChangedEvent,
    SessionInfoChangedEvent,
    SessionLoadedEvent,
    SessionEvent,
    TurnCompletedEvent,
)
from kernel.session.models import ConversationRecord
from kernel.session.runtime.helpers import (
    config_list as _config_list,
    decode_cursor as _decode_cursor,
    encode_cursor as _encode_cursor,
    get_git_branch as _get_git_branch,
)
from kernel.session.runtime.config_options import (
    MODE_CONFIG_ID,
    config_descriptors as _config_descriptors,
    mode_state as _mode_state,
    normalise_mode_id as _normalise_mode_id,
    validate_mode_id as _validate_mode_id,
)
from kernel.session.runtime.state import Session

UTC = timezone.utc
logger = logging.getLogger("kernel.session")
_CLIENT_TURN_ID_META_KEY = "mustang.agent/clientTurnId"
_CHARS_PER_TOKEN = 4


class SessionHandlerMixin(_SessionMixinBase):
    """ACP request handlers — one method per ``session/*`` request kind."""

    @staticmethod
    def _absolute_cwd_or_raise(cwd: str, *, field: str = "cwd") -> Path:
        path = Path(cwd)
        if not path.is_absolute():
            raise InvalidParams(f"{field} must be an absolute path")
        return path

    def _bind_connection_to_session(self, ctx: HandlerContext, session: Session) -> None:
        """Pin the WebSocket connection to ``session`` for routing + broadcasts.

        Args:
            ctx: Handler context whose ``conn`` and ``sender`` are bound.
            session: Session that will route updates through ``ctx.sender``.
        """
        ctx.conn.bound_session_id = session.session_id
        session.senders[ctx.conn.auth.connection_id] = ctx.sender

    async def new(self, ctx: HandlerContext, params: NewSessionParams) -> NewSessionResult:
        """Handle ACP ``session/new``: mint a session id and create the runtime.

        Args:
            ctx: Handler context for the requesting connection.
            params: ACP request body — ``cwd``, ``mcp_servers``, optional
                ``meta`` (for worktree setup).

        Returns:
            ``NewSessionResult`` carrying the new ``session_id``.
        """
        if params.mcp_servers:
            raise InvalidParams("session-scoped mcpServers are not supported yet")
        session_id = str(uuid.uuid4())
        cwd = self._absolute_cwd_or_raise(params.cwd)

        cwd = await self._maybe_create_worktree_session(session_id, cwd, params.meta)

        git_branch = _get_git_branch(cwd)
        session = await self._create_session(
            session_id=session_id,
            cwd=cwd,
            git_branch=git_branch,
            mcp_servers=params.mcp_servers,
        )
        self._bind_connection_to_session(ctx, session)

        return NewSessionResult(
            session_id=session_id,
            config_options=_config_descriptors(session.config_options, session.mode_id),
            modes=_mode_state(session.mode_id),
        )

    async def _maybe_create_worktree_session(
        self,
        session_id: str,
        cwd: Path,
        meta: dict[str, Any] | None,
    ) -> Path:
        """Allocate a git worktree for this session if ``meta`` requests one.

        Args:
            session_id: Owning session id, recorded in the worktree registry.
            cwd: Working directory the session would otherwise use.
            meta: ``params.meta`` — ``meta["mustang.agent/worktree"]`` carries
                ``slug`` and optional ``sparse_paths``.  ``meta["worktree"]``
                remains a compatibility alias during the ACPX migration.

        Returns:
            The new worktree path, or ``cwd`` unchanged when no worktree was
            requested, the Git subsystem is unavailable, or setup failed.
        """
        meta = meta or {}
        worktree_meta = meta.get("mustang.agent/worktree") or meta.get("worktree")
        if not worktree_meta:
            return cwd
        try:
            from kernel.git import GitManager
            from kernel.git.types import WorktreeSession
            from kernel.git.worktree import (
                create_worktree,
                find_git_root,
                setup_sparse_checkout,
                validate_slug,
            )

            git = self._module_table.get(GitManager)
            if not git.available:
                return cwd
            slug = worktree_meta["slug"]
            validate_slug(slug)
            root = await find_git_root(git, cwd)
            worktree_path, branch = await create_worktree(git, root, slug)
            if sparse_paths := worktree_meta.get("sparse_paths"):
                await setup_sparse_checkout(git, worktree_path, sparse_paths)
            await git.register_worktree(
                WorktreeSession(
                    session_id=session_id,
                    original_cwd=cwd,
                    worktree_path=worktree_path,
                    worktree_branch=branch,
                    slug=slug,
                    created_at=datetime.now(UTC),
                )
            )
            return worktree_path
        except (KeyError, ImportError):
            return cwd
        except Exception:
            logger.exception(
                "Worktree startup failed for session %s — using original cwd",
                session_id,
            )
            return cwd

    async def load_session(
        self, ctx: HandlerContext, params: LoadSessionParams
    ) -> LoadSessionResult:
        """Handle ACP ``session/load``: attach the connection and replay history.

        Reloads the session from disk if it was evicted, binds the new
        connection, replays the persisted event log so the client sees the
        full transcript, then appends a ``SessionLoadedEvent`` marker.

        Args:
            ctx: Handler context for the joining connection.
            params: ACP request body carrying ``session_id``.

        Returns:
            Empty ``LoadSessionResult`` once the replay completes.

        Raises:
            ResourceNotFoundError: ``params.session_id`` is not in the DB.
        """
        if params.mcp_servers:
            raise InvalidParams("session-scoped mcpServers are not supported yet")
        session_id = params.session_id
        if params.cwd is not None:
            self._absolute_cwd_or_raise(params.cwd)

        record = await self._store.get_session(session_id)
        if record is None:
            raise ResourceNotFoundError(f"Session not found: {session_id!r}")

        if session_id not in self._sessions:
            await self._load_from_disk(session_id)

        session = self._sessions[session_id]
        self._bind_connection_to_session(ctx, session)

        events = await self._store.read_events(session_id)
        await self._replay_events(ctx, session, events)
        if not any(isinstance(event, TurnCompletedEvent) for event in events):
            await self._replay_usage_snapshot(ctx, session, record)

        await self._write_event(session, SessionLoadedEvent)

        return LoadSessionResult(
            config_options=_config_descriptors(session.config_options, session.mode_id),
            modes=_mode_state(session.mode_id),
        )

    async def _replay_usage_snapshot(
        self, ctx: HandlerContext, session: Session, record: ConversationRecord
    ) -> None:
        total = record.total_input_tokens + record.total_output_tokens
        if total <= 0:
            return
        await ctx.sender.notify(
            "session/update",
            SessionUpdateNotification(
                session_id=session.session_id,
                update=UsageUpdate(
                    input_tokens=record.total_input_tokens,
                    output_tokens=record.total_output_tokens,
                ),
            ),
        )

    async def resume_session(
        self, ctx: HandlerContext, params: ResumeSessionParams
    ) -> ResumeSessionResult:
        """Handle ACP ``session/resume`` without replaying history.

        ``session/load`` remains the transcript replay path.  Resume only
        ensures the runtime is present, binds this connection, and returns the
        current mode/config view.
        """
        session_id = params.session_id
        if params.cwd is not None:
            self._absolute_cwd_or_raise(params.cwd)

        record = await self._store.get_session(session_id)
        if record is None:
            raise ResourceNotFoundError(f"Session not found: {session_id!r}")

        if session_id not in self._sessions:
            await self._load_from_disk(session_id)

        session = self._sessions[session_id]
        self._bind_connection_to_session(ctx, session)

        return ResumeSessionResult(
            config_options=_config_descriptors(session.config_options, session.mode_id),
            modes=_mode_state(session.mode_id),
            replayed=False,
        )

    def _cursor_start_index(
        self,
        records: list[ConversationRecord],
        cursor: str | None,
    ) -> int:
        """Return the index of the first record strictly after ``cursor``.

        Args:
            records: Sessions ordered by ``modified`` DESC then ``session_id``
                DESC (the shape ``_encode_cursor`` produced).
            cursor: Opaque cursor returned by a previous ``list`` call,
                or ``None`` for the first page.

        Raises:
            InvalidParams: ``cursor`` is malformed.
        """
        if cursor is None:
            return 0

        try:
            cursor_modified, cursor_id = _decode_cursor(cursor)
        except Exception:
            raise InvalidParams("Invalid session/list cursor") from None

        for index, record in enumerate(records):
            record_is_after_cursor = record.modified < cursor_modified or (
                record.modified == cursor_modified and record.session_id < cursor_id
            )
            if record_is_after_cursor:
                return index
        return 0

    def _list_page(
        self,
        records: list[ConversationRecord],
        *,
        cursor: str | None,
    ) -> tuple[list[ConversationRecord], str | None]:
        """Slice one page of size ``list_page_size`` from ``records``.

        Args:
            records: Pre-sorted list to page through.
            cursor: Cursor from the previous page, or ``None`` for the start.

        Returns:
            ``(page, next_cursor)``.  ``next_cursor`` is ``None`` when this
            slice already reached the end of ``records``.
        """
        start = self._cursor_start_index(records, cursor)
        page = records[start : start + self._flags.list_page_size]
        if start + self._flags.list_page_size >= len(records):
            return page, None

        last_record = page[-1]
        return page, _encode_cursor(last_record.modified, last_record.session_id)

    @staticmethod
    def _session_summaries(records: list[ConversationRecord]) -> list[SessionSummary]:
        return [SessionHandlerMixin._session_summary(record) for record in records]

    @staticmethod
    def _session_summary(record: ConversationRecord) -> SessionSummary:
        meta: dict[str, object] = {
            "createdAt": record.created,
            "totalInputTokens": record.total_input_tokens,
            "totalOutputTokens": record.total_output_tokens,
        }
        session_meta: dict[str, object] = {}
        if record.archived_at is not None:
            session_meta["archivedAt"] = record.archived_at
        if record.title_source is not None:
            session_meta["titleSource"] = record.title_source
        if session_meta:
            meta["mustang.agent/session"] = session_meta
        return SessionSummary(
            session_id=record.session_id,
            cwd=record.cwd,
            updated_at=record.modified,
            created_at=record.created,
            title=record.title,
            archived_at=record.archived_at,
            title_source=cast(Literal["auto", "user"] | None, record.title_source),
            meta=meta,
        )

    async def list(self, ctx: HandlerContext, params: ListSessionsParams) -> ListSessionsResult:
        """Handle ACP ``session/list``: paginated session summaries.

        Args:
            ctx: Handler context (unused beyond signature parity).
            params: ACP request body — optional ``cwd`` filter and
                opaque ``cursor`` for pagination.

        Returns:
            ``ListSessionsResult`` with one page of summaries plus the
            ``next_cursor`` (``None`` on the last page).
        """
        if params.cwd is not None:
            self._absolute_cwd_or_raise(params.cwd)
        records = await self._store.list_sessions(
            include_archived=params.include_archived,
            archived_only=params.archived_only,
        )

        if params.cwd:
            records = [record for record in records if record.cwd == params.cwd]

        page, next_cursor = self._list_page(records, cursor=params.cursor)
        return ListSessionsResult(sessions=self._session_summaries(page), next_cursor=next_cursor)

    async def get_usage(
        self, ctx: HandlerContext, params: GetUsageParams
    ) -> GetUsageResult:
        """Return the `/cost` usage dashboard payload for one session."""
        record = await self._store.get_session(params.session_id)
        if record is None:
            raise ResourceNotFoundError(f"Session not found: {params.session_id!r}")

        session = self._sessions.get(params.session_id)
        events = await self._store.read_events(params.session_id)
        latest_turn = _latest_completed_turn(events)
        latest_input = int(getattr(latest_turn, "input_tokens", 0) or 0)
        latest_output = int(getattr(latest_turn, "output_tokens", 0) or 0)
        context_tokens = latest_input + latest_output
        context_window = await self._context_window_for_usage(session)
        context_percent = (
            round((context_tokens / context_window) * 100, 1)
            if context_window and context_window > 0
            else 0.0
        )

        return GetUsageResult(
            session_id=record.session_id,
            title=record.title,
            cwd=record.cwd,
            created_at=record.created,
            updated_at=record.modified,
            model=_model_label_for_usage(session),
            kernel_version=events[-1].kernel_version if events else "",
            tokens=TokenUsageSummary(
                input=record.total_input_tokens,
                output=record.total_output_tokens,
                total=record.total_input_tokens + record.total_output_tokens,
            ),
            context=ContextUsageSummary(
                total_tokens=context_tokens,
                context_window=context_window,
                percent=context_percent,
                sections=_context_sections(events, latest_input, latest_output),
            ),
            history=_history_summary(events, session),
            memory=_memory_summary(self._module_table),
            environment=_environment_summary(session),
            cost_usd=None,
            cost_note="Pricing is not estimated until provider/model pricing tables are trusted.",
        )

    async def _context_window_for_usage(self, session: Session | None) -> int | None:
        if session is None:
            return None
        try:
            from kernel.llm import LLMManager

            llm = self._module_table.get(LLMManager)
            return await llm.context_window(session.orchestrator.config.model)
        except Exception:
            return None

    async def prompt(self, ctx: HandlerContext, params: PromptParams) -> PromptResult:
        """Handle ACP ``session/prompt``: run the turn now or queue it.

        When the session is idle the turn runs synchronously inside the
        request task.  Otherwise it joins the FIFO and the response is
        delivered via the queued turn's response future.

        Args:
            ctx: Handler context — ``request_id`` is recorded with the turn.
            params: ACP request body — ``session_id``, ``prompt`` blocks,
                optional ``max_turns``.

        Returns:
            ``PromptResult`` with the turn's stop reason.

        Raises:
            ResourceNotFoundError: session is not in memory.
            InternalError: queue depth has reached ``max_queue_length``.
        """
        session = self._get_or_raise(params.session_id)
        client_turn_id = _client_turn_id(params)
        if client_turn_id is not None:
            duplicate = await self._resolve_duplicate_prompt(session, client_turn_id, ctx)
            if duplicate is not None:
                return duplicate

        if session.in_flight_turn is None and not session.queue:
            return await self._run_turn_core(
                session,
                params,
                ctx.request_id,
                client_turn_id=client_turn_id,
            )

        if len(session.queue) >= self._flags.max_queue_length:
            raise InternalError("session prompt queue full")

        return await self._enqueue_turn(
            session,
            params,
            request_id=ctx.request_id,
            client_turn_id=client_turn_id,
        )

    async def _resolve_duplicate_prompt(
        self,
        session: Session,
        client_turn_id: str,
        ctx: HandlerContext,
    ) -> PromptResult | None:
        in_flight = session.in_flight_turn
        if in_flight is not None and in_flight.client_turn_id == client_turn_id:
            return await in_flight.completion_future

        for queued in session.queue:
            if queued.client_turn_id == client_turn_id:
                return await queued.response_future

        stored = await self._store.find_turn_by_client_turn_id(
            session.session_id,
            client_turn_id,
        )
        if stored is None:
            return None
        if stored.state == "completed" and stored.stop_reason is not None:
            await self._replay_completed_turn(ctx, session, client_turn_id)
            return PromptResult(
                stop_reason=stored.stop_reason,  # type: ignore[arg-type]
                meta=_turn_result_meta(client_turn_id, replayed=True),
            )
        raise InternalError(f"turn_incomplete: clientTurnId={client_turn_id}")

    async def _replay_completed_turn(
        self,
        ctx: HandlerContext,
        session: Session,
        client_turn_id: str,
    ) -> None:
        events = await self._store.read_events(session.session_id)
        in_turn = False
        for event in events:
            event_client_turn_id = getattr(event, "client_turn_id", None)
            if event.type == "user_message" and event_client_turn_id == client_turn_id:
                in_turn = True
                continue
            if not in_turn:
                continue
            if event.type == "turn_completed" and event_client_turn_id == client_turn_id:
                return
            await self._replay_event(ctx, session, event)

    async def _apply_mode_change(self, session: Session, mode_id: str) -> str:
        try:
            next_mode = _validate_mode_id(mode_id)
        except ValueError:
            raise InvalidParams(f"Unsupported session mode: {mode_id!r}") from None

        old_mode = _normalise_mode_id(session.mode_id)
        session.mode_id = next_mode
        session.config_options[MODE_CONFIG_ID] = next_mode
        session.orchestrator.set_mode(next_mode)

        await self._write_event(
            session,
            ModeChangedEvent,
            mode_id=next_mode,
            from_mode=old_mode,
        )
        full_state = dict(session.config_options)
        await self._write_event(
            session,
            ConfigOptionChangedEvent,
            config_id=MODE_CONFIG_ID,
            value=next_mode,
            full_state=full_state,
        )
        await self._broadcast(session, CurrentModeUpdate(mode_id=next_mode))
        await self._broadcast(session, ConfigOptionUpdate(config_options=_config_list(full_state)))
        return next_mode

    async def set_mode(self, ctx: HandlerContext, params: SetModeParams) -> SetModeResult:
        """Handle ACP ``session/set_mode``: switch the session mode and notify.

        Args:
            ctx: Handler context (unused beyond signature parity).
            params: ACP request body — ``session_id`` and the new ``mode_id``.

        Returns:
            Empty ``SetModeResult`` once the change is persisted and broadcast.

        Raises:
            ResourceNotFoundError: session is not in memory.
        """
        session = self._get_or_raise(params.session_id)
        await self._apply_mode_change(session, params.mode_id)
        return SetModeResult()

    async def set_config_option(
        self, ctx: HandlerContext, params: SetConfigOptionParams
    ) -> SetConfigOptionResult:
        """Handle ACP ``session/set_config_option``: update, persist, broadcast.

        Args:
            ctx: Handler context (unused beyond signature parity).
            params: ACP request body — ``session_id``, ``config_id``, ``value``.

        Returns:
            ``SetConfigOptionResult`` echoing the full config snapshot
            after the change.

        Raises:
            ResourceNotFoundError: session is not in memory.
        """
        if params.config_id != MODE_CONFIG_ID:
            raise InvalidParams(f"Unsupported session config option: {params.config_id!r}")

        session = self._get_or_raise(params.session_id)
        await self._apply_mode_change(session, params.value)
        return SetConfigOptionResult(
            config_options=_config_descriptors(session.config_options, session.mode_id)
        )

    async def rename_session(
        self, ctx: HandlerContext, params: RenameSessionParams
    ) -> RenameSessionResult:
        """Handle ``session/rename`` by setting a user-owned title."""
        title = params.title.strip()
        if not title:
            raise InvalidParams("session title must not be empty")
        if len(title) > 200:
            title = title[:200]

        session = await self._get_or_load(params.session_id)
        session.title = title
        await self._store.update_title(params.session_id, title, title_source="user")
        await self._write_event(session, SessionInfoChangedEvent, title=title)
        await self._broadcast(
            session,
            SessionInfoUpdate(
                title=title,
                updated_at=datetime.now(UTC).isoformat(),
                meta={"mustang.agent/session": {"titleSource": "user"}},
            ),
        )

        record = await self._store.get_session(params.session_id)
        if record is None:
            raise ResourceNotFoundError(f"Session not found: {params.session_id!r}")
        return RenameSessionResult.model_validate(self._session_summary(record).model_dump())

    async def archive_session(
        self, ctx: HandlerContext, params: ArchiveSessionParams
    ) -> ArchiveSessionResult:
        """Handle ``session/archive`` by toggling archive metadata."""
        record = await self._store.get_session(params.session_id)
        if record is None:
            raise ResourceNotFoundError(f"Session not found: {params.session_id!r}")

        archived_at = datetime.now(UTC).isoformat() if params.archived else None
        updated = await self._store.archive_session(params.session_id, archived_at)
        if not updated:
            raise ResourceNotFoundError(f"Session not found: {params.session_id!r}")

        session = self._sessions.get(params.session_id)
        if session is not None:
            await self._broadcast(
                session,
                SessionInfoUpdate(updated_at=datetime.now(UTC).isoformat()),
            )

        updated_record = await self._store.get_session(params.session_id)
        if updated_record is None:
            raise ResourceNotFoundError(f"Session not found: {params.session_id!r}")
        return ArchiveSessionResult.model_validate(
            self._session_summary(updated_record).model_dump()
        )

    async def close_session(
        self, ctx: HandlerContext, params: CloseSessionParams
    ) -> CloseSessionResult:
        """Handle ACP ``session/close``: release runtime, keep durable state."""
        record = await self._store.get_session(params.session_id)
        if record is None:
            raise ResourceNotFoundError(f"Session not found: {params.session_id!r}")

        session = self._sessions.get(params.session_id)
        if session is not None:
            if session.in_flight_turn is not None:
                session.in_flight_turn.task.cancel()
                session.in_flight_turn = None

            while session.queue:
                queued = session.queue.popleft()
                if not queued.response_future.done():
                    queued.response_future.set_result(PromptResult(stop_reason="cancelled"))

            session.senders.pop(ctx.conn.auth.connection_id, None)
            if ctx.conn.bound_session_id == params.session_id:
                ctx.conn.bound_session_id = None

            await self._close_runtime(session, quiet=False)
            self._sessions.pop(params.session_id, None)

            try:
                from kernel.tool_authz import ToolAuthorizer

                authorizer = self._module_table.get(ToolAuthorizer)
                authorizer.on_session_close(params.session_id)
            except (KeyError, ImportError):
                pass
            except Exception:
                logger.debug("authorizer.on_session_close failed during close")

        return CloseSessionResult()

    async def cancel(self, ctx: HandlerContext, params: CancelParams) -> None:
        """Handle ACP ``session/cancel``: stop the in-flight turn and drop the queue.

        ACP cancellation is a notification, not a request — unknown sessions
        are silently ignored rather than raising.  Queued turns resolve
        immediately with ``stop_reason="cancelled"``; the running turn's
        own ``finally`` block clears ``in_flight_turn``, so eviction is
        scheduled rather than performed inline.

        Args:
            ctx: Handler context (unused beyond signature parity).
            params: ACP notification body carrying ``session_id``.
        """
        session = self._sessions.get(params.session_id)
        if session is None:
            return

        if session.in_flight_turn is not None:
            session.in_flight_turn.task.cancel()

        while session.queue:
            queued = session.queue.popleft()
            if not queued.response_future.done():
                meta = (
                    _turn_result_meta(queued.client_turn_id, replayed=False)
                    if queued.client_turn_id is not None
                    else None
                )
                queued.response_future.set_result(
                    PromptResult(stop_reason="cancelled", meta=meta)
                )

        asyncio.create_task(self._maybe_evict(session))


def _client_turn_id(params: PromptParams) -> str | None:
    value = (params.meta or {}).get(_CLIENT_TURN_ID_META_KEY)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise InvalidParams("mustang.agent/clientTurnId must be a UUID") from None
    return str(parsed)


def _turn_result_meta(client_turn_id: str, *, replayed: bool) -> dict[str, object]:
    return {
        _CLIENT_TURN_ID_META_KEY: client_turn_id,
        "mustang.agent/replayedTurnResult": replayed,
    }


def _latest_completed_turn(events: list[SessionEvent]) -> SessionEvent | None:
    for event in reversed(events):
        if event.type == "turn_completed":
            return event
    return None


def _model_label_for_usage(session: Session | None) -> str | None:
    if session is None:
        return None
    model = session.orchestrator.config.model
    return f"{model.provider}/{model.model}"


def _history_summary(events: list[SessionEvent], session: Session | None) -> HistoryUsageSummary:
    completed_turns = [event for event in events if event.type == "turn_completed"]
    messages = sum(1 for event in events if event.type in {"user_message", "agent_message"})
    last_turn = completed_turns[-1] if completed_turns else None
    return HistoryUsageSummary(
        messages=messages,
        turns=len(completed_turns),
        tool_calls=sum(1 for event in events if event.type == "tool_call"),
        compactions=sum(1 for event in events if event.type == "conversation_snapshot"),
        queued_turns=len(session.queue) if session is not None else 0,
        in_flight=session.in_flight_turn is not None if session is not None else False,
        last_run_at=last_turn.timestamp.isoformat() if last_turn is not None else None,
        last_duration_ms=getattr(last_turn, "duration_ms", None),
    )


def _memory_summary(module_table: object) -> MemoryUsageSummary:
    try:
        from kernel.memory import MemoryManager

        memory = module_table.get(MemoryManager)  # type: ignore[attr-defined]
    except Exception:
        return MemoryUsageSummary()

    loaded = 1 if getattr(memory, "available", True) else 0
    return MemoryUsageSummary(loaded=loaded, writable_scopes=loaded)


def _environment_summary(session: Session | None) -> EnvironmentUsageSummary:
    return EnvironmentUsageSummary(
        lsp_servers=[],
        mcp_servers=[server.get("name", "") for server in (session.mcp_servers if session else []) if server],
    )


def _context_sections(
    events: list[SessionEvent],
    latest_input_tokens: int,
    latest_output_tokens: int,
) -> list[ContextUsageSection]:
    if latest_input_tokens <= 0 and latest_output_tokens <= 0:
        return [
            ContextUsageSection(id="system_prompt", label="System Prompt", tokens=0, percent=0.0),
            ContextUsageSection(id="memory", label="Memory", tokens=0, percent=0.0),
            ContextUsageSection(id="conversation", label="Conversation", tokens=0, percent=0.0),
            ContextUsageSection(id="tools", label="Tool Calls", tokens=0, percent=0.0),
        ]

    message_estimate = 0
    tool_estimate = 0
    for event in events:
        if event.type in {"user_message", "agent_message", "agent_thought"}:
            message_estimate += _estimate_event_tokens(event)
        elif event.type in {"tool_call", "tool_call_update"}:
            tool_estimate += _estimate_event_tokens(event)

    estimated_known = message_estimate + tool_estimate
    if estimated_known > 0 and latest_input_tokens > 0:
        scale = min(1.0, latest_input_tokens / estimated_known)
        conversation_tokens = int(message_estimate * scale)
        tool_tokens = int(tool_estimate * scale)
    else:
        conversation_tokens = latest_input_tokens
        tool_tokens = 0

    system_tokens = max(0, latest_input_tokens - conversation_tokens - tool_tokens)
    total = max(1, latest_input_tokens + latest_output_tokens)
    sections = [
        ContextUsageSection(id="system_prompt", label="System Prompt", tokens=system_tokens, percent=0.0),
        ContextUsageSection(id="memory", label="Memory", tokens=0, percent=0.0),
        ContextUsageSection(id="conversation", label="Conversation", tokens=conversation_tokens + latest_output_tokens, percent=0.0),
        ContextUsageSection(id="tools", label="Tool Calls", tokens=tool_tokens, percent=0.0),
    ]
    return [
        section.model_copy(update={"percent": round((section.tokens / total) * 100, 1)})
        for section in sections
    ]


def _estimate_event_tokens(event: SessionEvent) -> int:
    return max(0, len(_event_text(event)) // _CHARS_PER_TOKEN)


def _event_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_event_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_event_text(item) for item in value.values())
    if hasattr(value, "model_dump"):
        return _event_text(value.model_dump())  # type: ignore[attr-defined]
    return ""
