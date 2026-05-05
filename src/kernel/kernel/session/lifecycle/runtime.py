"""Session creation, lookup, eviction, and shutdown.

The lifecycle mixin owns the in-memory session map and the SQLite-backed
store.  Idle sessions are evicted from memory by ``_maybe_evict`` and
transparently reloaded by ``_get_or_load`` — eviction is invisible to
gateway callers but lets long-running kernels avoid unbounded memory.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kernel.protocol.interfaces.contracts.delete_session_params import DeleteSessionParams
from kernel.protocol.interfaces.contracts.delete_session_result import DeleteSessionResult
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.protocol.interfaces.errors import InvalidRequest, ResourceNotFoundError
from kernel.session._shared.base import _SessionMixinBase
from kernel.agent_runtime import NullAgentResourceView
from kernel.session.context import AgentContext
from kernel.session.events import KERNEL_VERSION, SessionCreatedEvent
from kernel.session.models import ConversationRecord
from kernel.session.runtime.flags import SessionFlags
from kernel.session.runtime.state import Session
from kernel.session.store import SessionStore

UTC = timezone.utc
logger = logging.getLogger("kernel.session")


class SessionLifecycleMixin(_SessionMixinBase):
    """Owns the in-memory session map, the SQLite store, and eviction."""

    async def startup(self) -> None:
        """Open the SQLite store, bind orchestrator prefs, init the session map."""
        self._flags: SessionFlags = self._module_table.flags.register("session", SessionFlags)
        self._agent_context = getattr(self, "_agent_context", None) or AgentContext.primary_compat(
            state_dir=self._module_table.state_dir
        )
        self._agent_resource_view = getattr(
            self,
            "_agent_resource_view",
            NullAgentResourceView(),
        )
        sessions_dir = self._agent_context.sessions_dir
        self._store = SessionStore(sessions_dir)
        await self._store.open()

        # SessionManager owns the orchestrator prefs section because orchestrators
        # are constructed per-session here, not by a dedicated subsystem.  Bind
        # failures are tolerated so a malformed config file does not block boot.
        from kernel.config.section import MutableSection
        from kernel.orchestrator.config_section import OrchestratorPrefs

        self._prefs_section: MutableSection[OrchestratorPrefs] | None
        try:
            self._prefs_section = self._module_table.config.bind_section(
                file="config",
                section="orchestrator",
                schema=OrchestratorPrefs,
            )
        except Exception:
            logger.exception(
                "SessionManager: could not bind OrchestratorPrefs — "
                "orchestrator language preference will be ignored"
            )
            self._prefs_section = None

        self._disconnect_llm_config: Any = None
        self._subscribe_llm_config()
        self._sessions: dict[str, Session] = {}
        logger.info(
            "SessionManager ready — agent=%s sessions DB at %s",
            self._agent_context.agent_id,
            sessions_dir,
        )

    def _subscribe_llm_config(self) -> None:
        """Keep active session orchestrators aligned with global model roles."""
        try:
            from kernel.llm.config import LLMConfig

            llm_section = self._module_table.config.get_section(
                file="kernel",
                section="llm",
                schema=LLMConfig,
            )
        except Exception:
            logger.exception(
                "SessionManager: could not subscribe to llm config — "
                "active sessions will keep their startup model until reloaded"
            )
            return

        async def _on_llm_config_change(old: LLMConfig, new: LLMConfig) -> None:
            self._sync_default_model_for_active_sessions(
                old.current_used.default,
                new.current_used.default,
            )

        self._disconnect_llm_config = llm_section.changed.connect(_on_llm_config_change)

    def _sync_default_model_for_active_sessions(
        self,
        old_default: Any,
        new_default: Any,
    ) -> None:
        """Move active sessions that were using the old default to the new one."""
        if new_default is None or old_default == new_default:
            return

        from kernel.llm.config import ModelRef
        from kernel.orchestrator.config import OrchestratorConfigPatch

        new_ref = ModelRef.model_validate(new_default)
        for session in list(self._sessions.values()):
            current = session.orchestrator.config.model
            if _model_refs_match(current, old_default) or (
                old_default is None and _is_placeholder_default(current)
            ):
                session.orchestrator.set_config(OrchestratorConfigPatch(model=new_ref))
                logger.info(
                    "SessionManager: session %s model synced to current_used.default [%s, %s]",
                    session.session_id,
                    new_ref.provider,
                    new_ref.model,
                )

    async def _create_session(
        self,
        session_id: str,
        cwd: Path,
        *,
        git_branch: str | None,
        mcp_servers: list[dict[str, Any]],
    ) -> Session:
        """Create an active session and persist its initial event atomically.

        Args:
            session_id: Pre-allocated id from the caller.
            cwd: Working directory the session operates in.
            git_branch: Branch name at creation time, or ``None`` if not
                inside a git repo.
            mcp_servers: MCP server configs to record in
                ``SessionCreatedEvent``.

        Returns:
            The new in-memory ``Session`` whose ``last_event_id`` already
            points at the persisted ``SessionCreatedEvent``.
        """
        orchestrator, task_registry = self._make_orchestrator(session_id, cwd, [], None)
        now = datetime.now(UTC)
        session = Session(
            session_id=session_id,
            cwd=cwd,
            created_at=now,
            updated_at=now,
            title=None,
            git_branch=git_branch,
            mode_id=None,
            config_options={},
            mcp_servers=mcp_servers,
            orchestrator=orchestrator,
            task_registry=task_registry,
        )
        self._sessions[session_id] = session

        event_id = "ev_" + uuid.uuid4().hex
        await self._store.create_session_with_events(
            ConversationRecord(session_id=session_id, cwd=str(cwd), title=None),
            [
                SessionCreatedEvent(
                    event_id=event_id,
                    parent_id=None,
                    timestamp=now,
                    session_id=session_id,
                    agent_depth=0,
                    kernel_version=KERNEL_VERSION,
                    cwd=str(cwd),
                    git_branch=git_branch,
                    mcp_servers=mcp_servers,
                )
            ],
        )
        session.last_event_id = event_id
        return session

    async def shutdown(self) -> None:
        """Tear down every active session and close the store.

        Each session's orchestrator and task registry are stopped; the
        ToolAuthorizer (if loaded) gets a chance to drop its per-session
        grant cache as we go.
        """
        disconnect = getattr(self, "_disconnect_llm_config", None)
        if callable(disconnect):
            disconnect()
            self._disconnect_llm_config = None

        try:
            from kernel.tool_authz import ToolAuthorizer

            authorizer = self._module_table.get(ToolAuthorizer)
        except (KeyError, ImportError):
            authorizer = None

        for session in list(self._sessions.values()):
            await self._close_runtime(session, quiet=False)
            if authorizer is not None:
                try:
                    authorizer.on_session_close(session.session_id)
                except Exception:
                    logger.debug("authorizer.on_session_close failed during shutdown")
        self._sessions.clear()
        await self._store.close()
        logger.info("SessionManager shut down")

    async def on_disconnect(self, connection_id: str) -> None:
        """Remove a closed connection from every session it was observing.

        Args:
            connection_id: Id of the WebSocket that just closed.

        Sessions that become idle as a result are evicted via
        ``_maybe_evict`` so memory does not grow with stale clients.
        """
        for session in list(self._sessions.values()):
            if connection_id in session.senders:
                session.senders.pop(connection_id)
                await self._maybe_evict(session)

    async def _maybe_evict(self, session: Session) -> None:
        """Evict a session from memory when it is fully idle.

        All three conditions must hold: no observing senders, no
        in-flight turn, empty queue.  The DB record and events are
        preserved so a later ``session/load`` reconstructs the session
        transparently.

        Args:
            session: Candidate to evict.  No-op if any condition fails or
                the session was already removed.
        """
        if (
            session.session_id in self._sessions
            and not session.senders
            and session.in_flight_turn is None
            and not session.queue
        ):
            logger.info(
                "session=%s evicting from memory (idle, no connections)",
                session.session_id,
            )
            await self._close_runtime(session, quiet=False)
            self._sessions.pop(session.session_id, None)

    async def delete_session(
        self,
        ctx_or_session_id: HandlerContext | str,
        params: DeleteSessionParams | None = None,
    ) -> DeleteSessionResult | bool:
        """Permanently delete a session from memory, DB, and sidecars.

        The legacy ``delete_session(session_id: str) -> bool`` form is kept
        for CronScheduler.  ACP calls use
        ``delete_session(ctx, DeleteSessionParams(...))`` and return a typed
        result object.

        Args:
            ctx_or_session_id: Either a session id (legacy internal call) or
                the handler context for an ACP request.
            params: ACP delete params when called through the protocol layer.

        Returns:
            ``bool`` for legacy callers, ``DeleteSessionResult`` for ACP.
        """
        if isinstance(ctx_or_session_id, str):
            deleted = await self._delete_session_by_id(ctx_or_session_id, force=True)
            return deleted

        if params is None:
            raise InvalidRequest("DeleteSessionParams required for ACP delete")
        deleted = await self._delete_session_by_id(
            params.session_id,
            force=params.force,
            connection_session_id=ctx_or_session_id.conn.bound_session_id,
        )
        return DeleteSessionResult(deleted=deleted)

    async def _delete_session_by_id(
        self,
        session_id: str,
        *,
        force: bool,
        connection_session_id: str | None = None,
    ) -> bool:
        session = self._sessions.get(session_id)
        if session is not None:
            active = bool(session.senders or session.in_flight_turn is not None or session.queue)
            deleting_bound_session = connection_session_id == session_id
            if (active or deleting_bound_session) and not force:
                raise InvalidRequest("session/delete requires force=true for active sessions")
            await self._close_runtime(session, quiet=True)
            self._sessions.pop(session_id, None)

        try:
            deleted = await self._store.delete_session(session_id)
        except Exception:
            logger.debug("delete_session(%s) — not found in DB", session_id)
            return False
        if deleted:
            shutil.rmtree(self._store.aux_dir(session_id), ignore_errors=True)
        return deleted

    def _get_or_raise(self, session_id: str) -> Session:
        """Return the in-memory session, raising if it has been evicted.

        Use ``_get_or_load`` when the caller can tolerate paying the disk
        reload — gateway turns do, ACP requests typically do not.

        Args:
            session_id: Session to look up.

        Returns:
            The active ``Session`` instance.

        Raises:
            ResourceNotFoundError: ``session_id`` is not currently in
                memory (either never created or evicted while idle).
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise ResourceNotFoundError(f"Session not active: {session_id!r}")
        return session

    async def _get_or_load(self, session_id: str) -> Session:
        """Return the in-memory session, reloading from disk if evicted.

        Gateway sessions are evicted from memory when idle (no WS
        connections, no queued turns); this helper transparently
        reconstructs them so ``run_turn_for_gateway`` works after an
        eviction cycle.

        Args:
            session_id: Session to look up or restore.

        Returns:
            The active ``Session`` instance, freshly loaded if necessary.

        Raises:
            ResourceNotFoundError: ``session_id`` does not exist in the
                DB (never created, not merely evicted).
        """
        if session_id in self._sessions:
            return self._sessions[session_id]
        record = await self._store.get_session(session_id)
        if record is None:
            raise ResourceNotFoundError(f"Session not found: {session_id!r}")
        await self._load_from_disk(session_id)
        return self._sessions[session_id]

    async def _close_runtime(self, session: Session, *, quiet: bool) -> None:
        """Stop a session's orchestrator and task registry.

        Args:
            session: Session whose runtime is being torn down.
            quiet: When ``True`` exception traces are suppressed — used
                during eviction and explicit deletion where teardown
                failures do not warrant a traceback.
        """
        for task in list(session.user_executions.values()):
            task.cancel()
        try:
            await session.orchestrator.close()
        except Exception:
            if not quiet:
                logger.exception(
                    "session=%s orchestrator close failed",
                    session.session_id,
                )
        if session.task_registry is not None:
            try:
                await session.task_registry.shutdown()
            except Exception:
                if not quiet:
                    logger.exception(
                        "session=%s task_registry shutdown failed",
                        session.session_id,
                    )
        try:
            from kernel.tools.builtin.python_tool import shutdown_python_worker

            shutdown_python_worker(session.session_id)
        except Exception:
            if not quiet:
                logger.exception(
                    "session=%s python worker shutdown failed",
                    session.session_id,
                )


def _model_refs_match(left: Any, right: Any) -> bool:
    """Compare model refs without requiring both objects to share a class."""
    if left is None or right is None:
        return left is right
    return getattr(left, "provider", None) == getattr(right, "provider", None) and getattr(
        left, "model", None
    ) == getattr(right, "model", None)


def _is_placeholder_default(model: Any) -> bool:
    """Return whether *model* is the no-config placeholder used by fresh installs."""
    return (
        getattr(model, "provider", None) == "default" and getattr(model, "model", None) == "default"
    )
