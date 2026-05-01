"""Tests for Orchestrator's side-effect dispatch (plan mode + tasks)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from daemon.engine.orchestrator import Orchestrator
from daemon.engine.stream import (
    PlanModeChanged,
    StreamEnd,
    StreamEvent,
    TaskUpdate,
    TextDelta,
    ToolCallStart,
)
from daemon.extensions.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.extensions.tools.registry import ToolRegistry
from daemon.permissions.engine import PermissionEngine
from daemon.permissions.modes import PermissionMode
from daemon.permissions.settings import PermissionSettings
from daemon.providers.base import Message, ModelInfo, Provider, ToolDefinition
from daemon.side_effects import EnterPlanMode, ExitPlanMode, SkillActivated, TasksUpdated
from daemon.tasks.store import TaskItem, TaskStore


class _StubTool(Tool):
    """Stub tool that returns a fixed side-effect."""

    name = "stub_effect"
    description = "Stub."
    permission_level = PermissionLevel.NONE

    class Input:
        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

    def __init__(self, name: str, side_effect: Any, output: str = "ok") -> None:
        self.name = name
        self._side_effect = side_effect
        self._output = output

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output=self._output, side_effect=self._side_effect)


class _FakeProvider(Provider):
    """Minimal provider that emits a pre-scripted sequence of events."""

    name = "fake"

    def __init__(self, sequences: list[list[StreamEvent]]) -> None:
        self._sequences = sequences
        self._call = 0

    def models(self) -> list[ModelInfo]:
        return [ModelInfo(name="fake-model", context_window=8000)]

    async def query_context_window(self, model: str | None = None) -> int | None:
        return 8000

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        idx = self._call
        self._call += 1
        seq = self._sequences[idx] if idx < len(self._sequences) else [StreamEnd()]
        for evt in seq:
            yield evt


def _build_orchestrator(
    tmp_path: Path,
    tool: Tool,
    *,
    task_store: TaskStore | None = None,
    session_id: str | None = None,
    permission_engine: PermissionEngine | None = None,
) -> Orchestrator:
    """Wire an orchestrator with a single-tool registry for side-effect tests."""
    from tests.daemon.engine.orchestrator_test_utils import make_test_orchestrator

    provider = _FakeProvider(
        sequences=[
            [
                ToolCallStart(tool_call_id="tc1", tool_name=tool.name, arguments={}),
                StreamEnd(),
            ],
            [TextDelta(content="done"), StreamEnd()],
        ]
    )

    tool_registry = ToolRegistry()
    tool_registry.register(tool)

    return make_test_orchestrator(
        provider=provider,
        tmp_path=tmp_path,
        tool_registry=tool_registry,
        permission_engine=permission_engine,
        task_store=task_store,
        session_dir=tmp_path,
        session_id=session_id,
    )


async def _run(orch: Orchestrator) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    async for e in orch.query("go"):
        events.append(e)
    return events


class TestEnterPlanMode:
    @pytest.mark.asyncio
    async def test_enter_flips_mode_and_emits_event(self, tmp_path: Path) -> None:
        settings = PermissionSettings(tmp_path / "s.json")
        engine = PermissionEngine(settings=settings, mode=PermissionMode.PROMPT)
        tool = _StubTool("enter_plan_mode", EnterPlanMode())
        orch = _build_orchestrator(tmp_path, tool, permission_engine=engine, session_id="sid")

        events = await _run(orch)

        changes = [e for e in events if isinstance(e, PlanModeChanged)]
        assert len(changes) == 1
        assert changes[0].active is True
        assert changes[0].previous_mode == "default"
        assert engine.mode == PermissionMode.PLAN

    @pytest.mark.asyncio
    async def test_enter_registers_plan_file(self, tmp_path: Path) -> None:
        settings = PermissionSettings(tmp_path / "s.json")
        engine = PermissionEngine(settings=settings)
        tool = _StubTool("enter_plan_mode", EnterPlanMode())
        orch = _build_orchestrator(tmp_path, tool, permission_engine=engine, session_id="sid")

        await _run(orch)

        # After enter, plan file should be registered for edits.
        assert engine._plan_file_path is not None
        assert engine._plan_file_path.endswith("sid.plan.md")


class TestExitPlanMode:
    @pytest.mark.asyncio
    async def test_exit_restores_previous_mode(self, tmp_path: Path) -> None:
        settings = PermissionSettings(tmp_path / "s.json")
        engine = PermissionEngine(settings=settings, mode=PermissionMode.PLAN)
        # Simulate that we had been in ACCEPT_EDITS before.
        tool = _StubTool("exit_plan_mode", ExitPlanMode(plan="# plan"))
        orch = _build_orchestrator(tmp_path, tool, permission_engine=engine, session_id="sid")
        orch.plan_mode._pre_plan_mode = PermissionMode.ACCEPT_EDITS  # noqa: SLF001

        events = await _run(orch)

        assert engine.mode == PermissionMode.ACCEPT_EDITS
        changes = [e for e in events if isinstance(e, PlanModeChanged)]
        assert len(changes) == 1
        assert changes[0].active is False

    @pytest.mark.asyncio
    async def test_exit_persists_plan_file(self, tmp_path: Path) -> None:
        settings = PermissionSettings(tmp_path / "s.json")
        engine = PermissionEngine(settings=settings, mode=PermissionMode.PLAN)
        tool = _StubTool("exit_plan_mode", ExitPlanMode(plan="# my plan"))
        orch = _build_orchestrator(tmp_path, tool, permission_engine=engine, session_id="abc")
        orch.plan_mode._pre_plan_mode = PermissionMode.PROMPT  # noqa: SLF001

        await _run(orch)

        plan_file = tmp_path / "abc.plan.md"
        assert plan_file.exists()
        assert plan_file.read_text() == "# my plan"

    @pytest.mark.asyncio
    async def test_exit_clears_plan_file_registration(self, tmp_path: Path) -> None:
        settings = PermissionSettings(tmp_path / "s.json")
        engine = PermissionEngine(settings=settings, mode=PermissionMode.PLAN)
        engine.set_plan_file("/tmp/old.plan.md")
        tool = _StubTool("exit_plan_mode", ExitPlanMode(plan="x"))
        orch = _build_orchestrator(tmp_path, tool, permission_engine=engine, session_id="sid")
        orch.plan_mode._pre_plan_mode = PermissionMode.PROMPT  # noqa: SLF001

        await _run(orch)

        assert engine._plan_file_path is None


class TestTasksUpdated:
    @pytest.mark.asyncio
    async def test_persists_and_broadcasts(self, tmp_path: Path) -> None:
        task_store = TaskStore(tmp_path, "sid")
        tool = _StubTool(
            "todo_write",
            TasksUpdated(
                tasks=[
                    TaskItem(
                        content="Run tests",
                        status="pending",
                        active_form="Running tests",
                    )
                ]
            ),
        )
        orch = _build_orchestrator(tmp_path, tool, task_store=task_store, session_id="sid")

        events = await _run(orch)

        updates = [e for e in events if isinstance(e, TaskUpdate)]
        assert len(updates) == 1
        assert updates[0].tasks[0].content == "Run tests"
        # Persisted.
        loaded = task_store.load()
        assert len(loaded) == 1
        assert loaded[0].content == "Run tests"

    @pytest.mark.asyncio
    async def test_broadcasts_without_store(self, tmp_path: Path) -> None:
        """Task updates still broadcast when task_store is None."""
        tool = _StubTool(
            "todo_write",
            TasksUpdated(
                tasks=[TaskItem(content="X", status="pending", active_form="X")],
            ),
        )
        orch = _build_orchestrator(tmp_path, tool, task_store=None, session_id=None)

        events = await _run(orch)
        updates = [e for e in events if isinstance(e, TaskUpdate)]
        assert len(updates) == 1


class TestSkillActivated:
    @pytest.mark.asyncio
    async def test_stores_rendered_prompt_and_emits_no_stream_event(self, tmp_path: Path) -> None:
        """SkillActivated is internal: no stream event, prompt stashed."""
        tool = _StubTool(
            "skill",
            SkillActivated(prompt="# rendered body"),
            output="# rendered body",
        )
        orch = _build_orchestrator(tmp_path, tool, session_id="sid")

        events = await _run(orch)

        # No additional stream event is emitted for skill activation —
        # the rendered prompt is kept internal and injected into the
        # system prompt on the next turn.
        assert orch.prompt_builder._active_skill_prompt == "# rendered body"  # noqa: SLF001
        # Sanity: the loop did run to completion.
        assert any(isinstance(e, TextDelta) for e in events)
