"""Typing base for SessionManager mixins.

Mixins are split across files but reference each other's attributes and
methods.  This module declares the shared surface so mypy can type-check
each mixin in isolation.

At type-check time ``_SessionMixinBase`` is a ``Protocol`` describing
the cross-mixin contract.  At runtime it is an empty class — real
attributes are bound by ``SessionLifecycleMixin.startup`` and the
concrete methods come from sibling mixins via MRO.  The runtime split
avoids ``Protocol`` metaclass conflicts when ``SessionManager`` also
inherits from ``Subsystem``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio
    from pathlib import Path
    from typing import Any, Protocol

    from kernel.config.section import MutableSection
    from kernel.orchestrator import Orchestrator, OrchestratorConfig
    from kernel.orchestrator.config_section import OrchestratorPrefs
    from kernel.orchestrator.types import (
        PermissionCallback,
        PermissionRequest,
        PermissionResponse,
    )
    from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
    from kernel.protocol.interfaces.contracts.prompt_params import PromptParams
    from kernel.protocol.interfaces.contracts.prompt_result import PromptResult
    from kernel.session.context import AgentContext
    from kernel.agent_runtime import AgentResourceView, NullAgentResourceView
    from kernel.session.events import SessionEvent
    from kernel.session.models import TokenUsageUpdate
    from kernel.session.runtime.flags import SessionFlags
    from kernel.session.runtime.state import Session
    from kernel.session.store import SessionStore

    class _SessionMixinBase(Protocol):
        """Shared attribute/method declarations for SessionManager mixins."""

        _store: SessionStore
        _sessions: dict[str, Session]
        _flags: SessionFlags
        _module_table: Any
        _prefs_section: MutableSection[OrchestratorPrefs] | None
        _disconnect_llm_config: Any
        _agent_context: AgentContext
        _agent_resource_view: AgentResourceView | NullAgentResourceView

        async def _write_event(
            self,
            session: Session,
            event_cls: type,
            *,
            tokens: TokenUsageUpdate | None = None,
            **kwargs: Any,
        ) -> str: ...

        async def _broadcast(self, session: Session, update: Any) -> None: ...

        async def _drain_pending_mode_changes(self, session: Session) -> None: ...

        @staticmethod
        def _blocks_to_raw(blocks: list[Any]) -> list[dict[str, Any]]: ...

        def _maybe_spill(
            self,
            session: Session,
            tool_call_id: str,
            content_raw: list[dict[str, Any]],
        ) -> list[dict[str, Any]]: ...

        async def _create_session(
            self,
            session_id: str,
            cwd: Path,
            *,
            git_branch: str | None,
            mcp_servers: list[dict[str, Any]],
        ) -> Session: ...

        async def _close_runtime(self, session: Session, *, quiet: bool) -> None: ...

        async def _maybe_evict(self, session: Session) -> None: ...

        def _get_or_raise(self, session_id: str) -> Session: ...

        async def _get_or_load(self, session_id: str) -> Session: ...

        async def _load_from_disk(self, session_id: str) -> None: ...

        def _make_orchestrator(
            self,
            session_id: str,
            cwd: Path,
            initial_history: list[Any],
            config: OrchestratorConfig | None,
        ) -> tuple[Orchestrator, Any]: ...

        def deliver_message(
            self,
            target_session_id: str,
            message: str,
            *,
            sender_session_id: str | None = None,
        ) -> bool: ...

        def _explicit_replay_keys(
            self, events: list[SessionEvent]
        ) -> tuple[set[str], set[str], set[str], set[str]]: ...

        async def _replay_event(
            self,
            ctx: HandlerContext,
            session: Session,
            event: SessionEvent,
            *,
            skip_conversation_users: bool = False,
            skip_conversation_tools: bool = False,
            skip_conversation_user_texts: set[str] | None = None,
            skip_conversation_agent_texts: set[str] | None = None,
            skip_conversation_thought_texts: set[str] | None = None,
            skip_conversation_tool_ids: set[str] | None = None,
        ) -> None: ...

        async def _replay_events(
            self, ctx: HandlerContext, session: Session, events: list[SessionEvent]
        ) -> None: ...

        async def _handle_orchestrator_event(
            self,
            session: Session,
            event: Any,
            accumulated_text: list[str],
            accumulated_thought: list[str],
        ) -> None: ...

        def _enqueue_turn(
            self,
            session: Session,
            params: PromptParams,
            *,
            request_id: str | int | None,
            client_turn_id: str | None = None,
            text_collector: asyncio.Future[Any] | None = None,
            on_permission: PermissionCallback | None = None,
        ) -> asyncio.Future[Any]: ...

        async def _run_turn_core(
            self,
            session: Session,
            params: PromptParams,
            request_id: str | int | None,
            *,
            client_turn_id: str | None = None,
            text_collector: asyncio.Future[Any] | None = None,
            on_permission_override: PermissionCallback | None = None,
        ) -> PromptResult: ...

        async def _on_permission(
            self, session: Session, req: PermissionRequest
        ) -> PermissionResponse: ...

        async def _maybe_create_worktree_session(
            self,
            session_id: str,
            cwd: Path,
            meta: Any,
        ) -> Path: ...

else:

    class _SessionMixinBase:
        """Runtime no-op base; the type-check surface lives in the Protocol above."""
