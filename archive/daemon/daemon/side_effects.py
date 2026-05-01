"""Tool side-effect ADT — typed requests from tools to the orchestrator.

A tool that needs to mutate orchestrator state (switch permission
mode, persist task list, etc.) returns a ``side_effect`` value in
its :class:`~daemon.extensions.tools.base.ToolResult`.  The
orchestrator dispatches on the side-effect type with a single
``match`` statement and performs the action.

This keeps the tool file self-contained (its full effect is visible
in the ``execute()`` return value) and gives the orchestrator a
closed, type-checked set of actions to handle — no
``if tc.name == "..."`` string matching, no callbacks leaking into
:class:`~daemon.extensions.tools.base.ToolContext`.

Adding a new state-mutating tool:
  1. Add a new frozen BaseModel variant below with a unique ``type``
     discriminator.
  2. Add the variant to the ``SideEffect`` union.
  3. Add a ``case`` arm in :meth:`Orchestrator._dispatch_side_effect`.

The discriminated union mirrors the pattern used by
:mod:`daemon.engine.stream` events.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from daemon.tasks.store import TaskItem


class EnterPlanMode(BaseModel):
    """Tool requests the orchestrator enter PLAN permission mode.

    Emitted by :class:`EnterPlanModeTool`.  The orchestrator saves
    the current mode as ``pre_plan_mode`` and switches to
    :attr:`~daemon.permissions.modes.PermissionMode.PLAN`.
    """

    type: Literal["enter_plan_mode"] = "enter_plan_mode"


class ExitPlanMode(BaseModel):
    """Tool requests the orchestrator restore the pre-plan mode.

    Emitted by :class:`ExitPlanModeTool` once the LLM has produced a
    final plan.  The orchestrator restores ``pre_plan_mode`` and
    persists the plan to ``{session}.plan.md``.

    Attributes:
        plan: The finalized implementation plan in markdown.
    """

    type: Literal["exit_plan_mode"] = "exit_plan_mode"
    plan: str


class SkillActivated(BaseModel):
    """Tool signals that a skill was activated this turn.

    Emitted by :class:`SkillTool` after rendering a skill body.  The
    orchestrator stores the rendered prompt and injects it into the
    system prompt for subsequent turns — without needing to know
    anything about the tool's name.  Replaces the previous
    ``if tc.name == "skill"`` special-case.

    Attributes:
        prompt: Rendered skill body (already argument-substituted)
            to merge into the system prompt.
    """

    type: Literal["skill_activated"] = "skill_activated"
    prompt: str


class TasksUpdated(BaseModel):
    """Tool signals a new task list for the session.

    Emitted by :class:`TodoWriteTool`.  Carries the full list so
    the orchestrator can persist it to its session-scoped
    :class:`TaskStore` (the tool itself is stateless and shared
    across sessions) and broadcast a
    :class:`~daemon.engine.stream.TaskUpdate` event to WS clients.

    Attributes:
        tasks: Full task list as typed :class:`TaskItem` models.
            Validated at tool-result boundary; the orchestrator
            persists and re-broadcasts without a second
            round-trip through dicts.
    """

    type: Literal["tasks_updated"] = "tasks_updated"
    tasks: list[TaskItem]


class FileChanged(BaseModel):
    """Tool signals a file was written or edited.

    Emitted by :class:`FileEditTool` and :class:`FileWriteTool`
    after a successful operation.  The tool executor dispatches this
    to fire the ``file_changed`` hook (which has access to the hook
    registry — tools themselves do not).

    Attributes:
        file_path: Absolute path to the changed file.
        change_type: ``"edit"`` or ``"write"``.
    """

    type: Literal["file_changed"] = "file_changed"
    file_path: str
    change_type: str  # "edit" | "write"


# Discriminated union of all side-effect types.  Orchestrator
# pattern-matches on this union — mypy will flag missing cases.
SideEffect = Annotated[
    EnterPlanMode | ExitPlanMode | TasksUpdated | SkillActivated | FileChanged,
    Field(discriminator="type"),
]
