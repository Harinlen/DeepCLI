"""Build the per-session ``Orchestrator`` and its dependency bundle.

Wires optional subsystems (Tools, Skills, Hooks, Memory, MCP, Git, …) and
captures session-bound closures for mode changes, reminder buffering,
cross-session messaging, and prompt-avoidance signalling.  Subsystems
that failed to load are tolerated as ``None`` so the kernel can still
serve sessions in degraded mode.
"""

from __future__ import annotations

import builtins
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from kernel.llm.config import ModelRef
from kernel.orchestrator import Orchestrator, OrchestratorConfig
from kernel.session._shared.base import _SessionMixinBase

from kernel.session.runtime.helpers import (
    make_summarise_closure as _make_summarise_closure,
)

if TYPE_CHECKING:
    from kernel.orchestrator.orchestrator import StandardOrchestrator

logger = logging.getLogger("kernel.session")


class SessionOrchestratorFactoryMixin(_SessionMixinBase):
    """Builds a ``StandardOrchestrator`` and its task registry per session."""

    def _optional_subsystem(self, module_path: str, class_name: str) -> Any:
        """Look up an optional subsystem, returning ``None`` if unavailable.

        Used for everything except the LLM provider so the kernel can run
        with subsystems that failed to load — the orchestrator falls back
        to safe defaults (allow-all tool authz, empty skill set, …).

        Args:
            module_path: Dotted module path to import, e.g. ``"kernel.tools"``.
            class_name: Subsystem class name within that module.

        Returns:
            The registered subsystem instance, or ``None`` if the module
            is missing, the class is not exported, or the subsystem is
            not registered.
        """
        try:
            module = __import__(module_path, fromlist=[class_name])
            return self._module_table.get(getattr(module, class_name))
        except (AttributeError, KeyError, ImportError):
            return None

    def _make_orchestrator(
        self,
        session_id: str,
        cwd: Path,
        initial_history: "builtins.list[Any]",
        config: OrchestratorConfig | None,
    ) -> tuple[Orchestrator, Any]:
        """Build a ``StandardOrchestrator`` and its task registry for one session.

        Resolves subsystems from the module table, captures session-bound
        closures (mode changes, reminder buffer, cross-session delivery,
        prompt avoidance), and wires them into ``OrchestratorDeps``.

        Args:
            session_id: Owning session id, captured by the closures so
                they reach the right ``Session`` later.
            cwd: Working directory the orchestrator runs in.
            initial_history: Pre-existing history loaded from the event
                log on resume; empty list for a fresh session.
            config: Pre-built orchestrator config.  When ``None`` the
                factory builds one from ``LLMManager`` and the user
                preferences section.

        Returns:
            ``(orchestrator, task_registry)`` ready to be installed on
            the new ``Session``.
        """
        from kernel.llm import LLMManager
        from kernel.orchestrator.orchestrator import StandardOrchestrator
        from kernel.orchestrator.types import OrchestratorDeps

        # ``provider`` (LLMManager) is the only required dep; every other
        # subsystem is optional and defaults to None when unavailable, in
        # which case ToolExecutor falls back to allow-all / empty pool.
        llm_manager = self._module_table.get(LLMManager)

        tool_source = self._optional_subsystem("kernel.tools", "ToolManager")
        authorizer = self._optional_subsystem("kernel.tool_authz", "ToolAuthorizer")

        # Idempotent: registers this session's grant-cache bucket.
        if authorizer is not None:
            try:
                authorizer.on_session_open(session_id)
            except Exception:
                logger.exception("authorizer.on_session_open failed — continuing with empty cache")

        skills = self._optional_subsystem("kernel.skills", "SkillManager")
        hooks = self._optional_subsystem("kernel.hooks", "HookManager")
        memory = self._optional_subsystem("kernel.memory", "MemoryManager")
        _schedule_manager = self._optional_subsystem("kernel.schedule", "ScheduleManager")
        _git_manager = self._optional_subsystem("kernel.git", "GitManager")
        _mcp_manager = self._optional_subsystem("kernel.mcp", "MCPManager")

        def _mcp_instructions() -> list[tuple[str, str]]:
            if _mcp_manager is None:
                return []
            return [
                (connection.name, connection.instructions)
                for connection in _mcp_manager.get_connected()
                if connection.instructions
            ]

        # Reminder buffer: ToolExecutor enqueues ``<system-reminder>`` blocks
        # produced by hooks here; the Orchestrator drains them at the start
        # of each turn so they ride into the next LLM request.
        def _queue_reminders(reminders: list[str]) -> None:
            if not reminders:
                return
            session = self._sessions.get(session_id)
            if session is not None:
                session.pending_reminders.extend(reminders)

        def _drain_reminders() -> list[str]:
            session = self._sessions.get(session_id)
            if session is None:
                return []
            reminders = list(session.pending_reminders)
            session.pending_reminders.clear()
            return reminders

        # True when nobody is listening (no WS connection, no gateway sender).
        # Sub-agents share the closure so the signal propagates down the tree.
        def _should_avoid_prompts() -> bool:
            session = self._sessions.get(session_id)
            if session is None:
                return True
            return not session.senders

        def _set_mode(mode: str) -> None:
            """Switch the session's mode synchronously from inside a tool.

            The resulting ``ModeChangedEvent`` and broadcast are queued
            on the session and flushed by ``_drain_pending_mode_changes``
            at the start of the next turn — this closure must not await.

            Args:
                mode: New mode id, or the ``"restore"`` sentinel which
                    pops back to ``pre_plan_mode`` (used by
                    ``ExitPlanMode``); falls through to ``"default"`` if
                    no prior mode was recorded.
            """
            session = self._sessions.get(session_id)
            if session is None:
                return
            old_mode = session.mode_id

            if mode == "restore":
                mode = session.pre_plan_mode or "default"
                session.pre_plan_mode = None

            entered_plan_mode = mode == "plan" and old_mode != "plan"
            exited_plan_mode = old_mode == "plan" and mode != "plan"

            if entered_plan_mode:
                session.pre_plan_mode = old_mode
                session.needs_plan_mode_exit_attachment = False
            elif exited_plan_mode:
                session.has_exited_plan_mode = True
                session.needs_plan_mode_exit_attachment = True

            session.mode_id = mode
            session.orchestrator.set_mode(mode)

            # Mirror plan-mode flags onto the orchestrator so
            # ``_inject_plan_mode_prompts`` does not need a back-reference
            # to Session.  These fields live on the concrete
            # StandardOrchestrator, not the Protocol.
            orch = cast("StandardOrchestrator", session.orchestrator)
            orch._has_exited_plan_mode = session.has_exited_plan_mode
            orch._needs_plan_mode_exit_attachment = session.needs_plan_mode_exit_attachment

            if entered_plan_mode:
                orch._plan_mode_turn_count = 0
                orch._plan_mode_attachment_count = 0

            session.pending_mode_changes.append((old_mode, mode))

        from kernel.tasks.registry import TaskRegistry

        task_registry = TaskRegistry()

        def _deliver_cross_session(target_id: str, message: str) -> bool:
            return self.deliver_message(
                target_id,
                message,
                sender_session_id=session_id,
            )

        def _route_agent_message(target_agent_id: str, message: str) -> bool:
            agent_hub = getattr(self._module_table, "agent_hub", None)
            if agent_hub is None:
                return False

            from kernel.agents import (
                CallerIdentity,
                CallerIdentityKind,
                RouterFrame,
                RouterFrameKind,
                RouterTarget,
            )

            caller_agent_id = self._agent_context.agent_id
            routed = agent_hub.router.route_message(
                RouterFrame(
                    frame_id=f"send-message-{uuid.uuid4().hex}",
                    kind=RouterFrameKind.AGENT_MESSAGE,
                    source=f"agent:{caller_agent_id}",
                    target=RouterTarget(agent_id=target_agent_id),
                    caller=CallerIdentity(
                        kind=CallerIdentityKind.DURABLE_AGENT,
                        subject_id=f"agent:{caller_agent_id}",
                        agent_id=caller_agent_id,
                    ),
                    session_id=session_id,
                    payload={"text": message},
                )
            )
            return routed is not None

        # Summarisation hook for WebFetch and similar tools that need a
        # one-shot completion using the ``compact`` role (falling back to
        # ``default`` when unset).
        summarise_fn = _make_summarise_closure(llm_manager)

        deps = OrchestratorDeps(
            provider=llm_manager,
            tool_source=tool_source,
            authorizer=authorizer,
            should_avoid_prompts_provider=_should_avoid_prompts,
            memory=memory,
            skills=skills,
            hooks=hooks,
            set_mode=_set_mode,
            queue_reminders=_queue_reminders,
            drain_reminders=_drain_reminders,
            prompts=self._module_table.prompts,
            task_registry=task_registry,
            deliver_cross_session=_deliver_cross_session,
            route_agent_message=_route_agent_message,
            schedule_manager=_schedule_manager,
            mcp=_mcp_manager,
            git=_git_manager,
            summarise=summarise_fn,
            mcp_instructions=_mcp_instructions,
        )

        # Read prefs on every build instead of caching in ``startup`` so
        # the next session benefits from any config hot-reload.  Only used
        # when the caller did not hand us a pre-built ``OrchestratorConfig``.
        prefs_language: str | None = None
        prefs_section = getattr(self, "_prefs_section", None)
        if prefs_section is not None:
            try:
                prefs_language = prefs_section.get().language
            except Exception:
                logger.debug("prefs_section.get() failed — treating language as unset")

        if llm_manager:
            try:
                default_model = llm_manager.model_for("default")
            except KeyError:
                default_model = ModelRef(provider="default", model="default")
        else:
            default_model = ModelRef(provider="default", model="default")

        orchestrator: Orchestrator = StandardOrchestrator(
            deps=deps,
            session_id=session_id,
            initial_history=initial_history,
            config=config
            or OrchestratorConfig(
                model=default_model,
                temperature=None,
                language=prefs_language,
            ),
            cwd=cwd,
        )
        return orchestrator, task_registry
