"""Plan-mode state machine.

Encapsulates entering/exiting plan mode, permission mode transitions,
and plan file persistence.  The :class:`Orchestrator` delegates all
plan-mode operations to this controller.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncIterator

from daemon.engine.stream import (
    PermissionModeChanged,
    PlanModeChanged,
    StreamEvent,
)
from daemon.permissions.engine import PermissionEngine
from daemon.permissions.modes import PermissionMode
from daemon.side_effects import EnterPlanMode, ExitPlanMode

logger = logging.getLogger(__name__)


class PlanModeController:
    """Manages plan-mode lifecycle for a single session.

    Owns the pre-plan-mode backup and first-turn flag.  Does *not*
    own the :class:`PermissionEngine` — it mutates the shared engine
    that the :class:`ToolExecutor` also reads.

    Args:
        permission_engine: Shared permission engine (read+write).
        session_dir: Directory for persisting the plan file.
        session_id: Session identifier for the plan filename.
    """

    def __init__(
        self,
        permission_engine: PermissionEngine,
        session_dir: Path | None = None,
        session_id: str | None = None,
    ) -> None:
        self._permission_engine = permission_engine
        self._session_dir = session_dir
        self._session_id = session_id
        self._pre_plan_mode: PermissionMode | None = None
        self._first_turn: bool = False

    # -- Public properties -------------------------------------------------

    @property
    def in_plan_mode(self) -> bool:
        """``True`` while the session is in plan mode."""
        return self._permission_engine.mode == PermissionMode.PLAN

    @property
    def first_turn(self) -> bool:
        """``True`` on the first LLM turn after entering plan mode.

        Used by :class:`SystemPromptBuilder` to decide whether to
        emit the full planning instructions or the short reminder.
        Consumed (set to ``False``) by :meth:`consume_first_turn`.
        """
        return self._first_turn

    def consume_first_turn(self) -> None:
        """Mark the first-turn flag as consumed."""
        self._first_turn = False

    # -- Side-effect dispatch ----------------------------------------------

    async def dispatch_side_effect(
        self,
        effect: EnterPlanMode | ExitPlanMode,
    ) -> AsyncIterator[StreamEvent]:
        """Handle an :class:`EnterPlanMode` or :class:`ExitPlanMode` effect."""
        if isinstance(effect, EnterPlanMode):
            async for evt in self._enter():
                yield evt
        elif isinstance(effect, ExitPlanMode):
            async for evt in self._exit(effect.plan):
                yield evt

    # -- Public entry points (for WS commands) -----------------------------

    async def enter(self) -> AsyncIterator[StreamEvent]:
        """Enter plan mode (``/plan`` command or LLM tool)."""
        async for evt in self._enter():
            yield evt

    async def exit(self, plan: str = "") -> AsyncIterator[StreamEvent]:
        """Exit plan mode (``/plan exit`` or LLM tool)."""
        async for evt in self._exit(plan):
            yield evt

    async def set_mode(self, mode: PermissionMode) -> AsyncIterator[StreamEvent]:
        """Switch the active permission mode (Step 5.8).

        Handles PLAN specially — delegates to enter/exit so the plan
        file and read-only restrictions stay in one place.
        """
        current = self._permission_engine.mode
        if mode == current:
            return

        if mode == PermissionMode.PLAN:
            async for evt in self._enter():
                yield evt
            yield PermissionModeChanged(
                mode=PermissionMode.PLAN.value,
                previous_mode=current.value,
            )
            return

        if current == PermissionMode.PLAN:
            async for evt in self._exit(""):
                yield evt
            # Override with the user's requested target.
            self._permission_engine.mode = mode
            yield PermissionModeChanged(
                mode=mode.value,
                previous_mode=PermissionMode.PLAN.value,
            )
            return

        self._permission_engine.mode = mode
        yield PermissionModeChanged(mode=mode.value, previous_mode=current.value)

    # -- Internal ----------------------------------------------------------

    async def _enter(self) -> AsyncIterator[StreamEvent]:
        previous = self._permission_engine.mode
        self._pre_plan_mode = previous
        self._permission_engine.mode = PermissionMode.PLAN
        self._first_turn = True
        if self._session_dir and self._session_id:
            plan_path = self._session_dir / f"{self._session_id}.plan.md"
            self._permission_engine.set_plan_file(str(plan_path))
        yield PlanModeChanged(active=True, previous_mode=previous.value)

    async def _exit(self, plan_text: str) -> AsyncIterator[StreamEvent]:
        restore = self._pre_plan_mode or PermissionMode.PROMPT
        self._permission_engine.mode = restore
        self._pre_plan_mode = None
        self._first_turn = False
        self._permission_engine.set_plan_file(None)
        if self._session_dir and self._session_id and plan_text:
            plan_path = self._session_dir / f"{self._session_id}.plan.md"
            try:
                plan_path.parent.mkdir(parents=True, exist_ok=True)
                plan_path.write_text(plan_text, encoding="utf-8")
            except OSError:
                logger.exception("Failed to persist plan to %s", plan_path)
        yield PlanModeChanged(active=False, previous_mode="plan")
