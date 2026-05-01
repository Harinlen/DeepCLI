from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from kernel.schedule.types import CronTask, Schedule, ScheduleKind
from kernel.tools.builtin.cmd import CmdTool
from kernel.tools.builtin.cron_create import CronCreateTool
from kernel.tools.builtin.cron_delete import CronDeleteTool
from kernel.tools.builtin.cron_list import CronListTool
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache
from kernel.tools.types import ToolCallResult, ToolInputError


class _RiskCtx:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.session_id = "session-1"


def _ctx(tmp_path: Path, schedule_manager: Any = None) -> ToolContext:
    return ToolContext(
        session_id="session-1",
        agent_depth=0,
        agent_id=None,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
        schedule_manager=schedule_manager,
    )


async def _single(tool: Any, input: dict[str, Any], ctx: ToolContext) -> ToolCallResult:
    events: list[ToolCallResult] = []
    async for event in tool.call(input, ctx):
        events.append(event)
    assert len(events) == 1
    return events[0]


class _FakeScheduleManager:
    def __init__(self) -> None:
        self.create_kwargs: dict[str, Any] | None = None
        self.include_completed: bool | None = None
        self.delete_id: str | None = None
        self.tasks: list[CronTask] = [
            CronTask(
                id="job-1",
                schedule=Schedule(kind=ScheduleKind.every, interval_seconds=1800),
                prompt="say hello" * 40,
                description="greeting",
                recurring=True,
                durable=True,
                next_fire_at=1_800_000_000.0,
                fire_count=2,
            )
        ]

    async def create_task(self, **kwargs: Any) -> CronTask:
        self.create_kwargs = kwargs
        return CronTask(
            id="created-1",
            schedule=Schedule(kind=ScheduleKind.every, interval_seconds=kwargs["repeat_duration_seconds"]),
            prompt=kwargs["prompt"],
            description=kwargs["description"],
            recurring=True,
            durable=kwargs["durable"],
            next_fire_at=time.time() + 60,
        )

    async def list_tasks(self, *, include_completed: bool) -> list[CronTask]:
        self.include_completed = include_completed
        return self.tasks

    async def delete_task(self, task_id: str) -> bool:
        self.delete_id = task_id
        return task_id == "job-1"


class TestCmdTool:
    def test_default_risk_classifies_empty_safe_compound_dangerous_and_unknown(
        self,
        tmp_path: Path,
    ) -> None:
        tool = CmdTool()
        ctx = _RiskCtx(tmp_path)

        assert tool.default_risk({"command": ""}, ctx).default_decision == "ask"
        assert tool.default_risk({"command": "dir"}, ctx).default_decision == "allow"
        assert tool.default_risk({"command": "echo a && echo b"}, ctx).reason.startswith("compound")
        assert tool.default_risk({"command": "del C:\\tmp\\x"}, ctx).default_decision == "deny"
        assert tool.default_risk({"command": "custom"}, ctx).default_decision == "ask"

    def test_permission_matcher_supports_exact_and_prefix_patterns(self) -> None:
        matcher = CmdTool().prepare_permission_matcher({"command": "git status --short"})

        assert matcher("git:*")
        assert matcher("git status --short")
        assert not matcher("npm:*")

    async def test_validate_input_and_missing_cmd_executable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        tool = CmdTool()
        with pytest.raises(ToolInputError, match="non-empty"):
            await tool.validate_input({"command": "   "}, _RiskCtx(tmp_path))
        with pytest.raises(ToolInputError, match="32,000"):
            await tool.validate_input({"command": "x" * 32_001}, _RiskCtx(tmp_path))

        await tool.validate_input({"command": "echo hi"}, _RiskCtx(tmp_path))
        monkeypatch.setattr(shutil, "which", lambda name: None)
        result = await _single(tool, {"command": "echo hi"}, _ctx(tmp_path))

        assert result.data["exit_code"] == -1
        assert "cmd.exe not found" in result.display.text


class TestCronTools:
    async def test_cron_create_requires_schedule_manager(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="Schedule subsystem"):
            await _single(CronCreateTool(), {"schedule": "every 1h", "prompt": "hello"}, _ctx(tmp_path))

    async def test_cron_create_parses_repeat_limits_and_returns_display(self, tmp_path: Path) -> None:
        manager = _FakeScheduleManager()
        result = await _single(
            CronCreateTool(),
            {
                "schedule": "every 1h",
                "prompt": "hello",
                "description": "desc",
                "durable": False,
                "repeat_duration": "30m",
                "repeat_until": "2026-05-01T12:00:00",
                "delivery": "none",
            },
            _ctx(tmp_path, manager),
        )

        assert manager.create_kwargs is not None
        assert manager.create_kwargs["schedule_expr"] == "every 1h"
        assert manager.create_kwargs["repeat_duration_seconds"] == pytest.approx(1800, abs=0.01)
        assert manager.create_kwargs["delivery"] == "none"
        assert result.data["id"] == "created-1"
        assert "Created cron job created-1" in result.display.text

    async def test_cron_create_rejects_bad_repeat_options(self, tmp_path: Path) -> None:
        manager = _FakeScheduleManager()
        with pytest.raises(ToolInputError, match="Invalid repeat_duration"):
            await _single(
                CronCreateTool(),
                {"schedule": "every 1h", "prompt": "hello", "repeat_duration": "nope"},
                _ctx(tmp_path, manager),
            )

        with pytest.raises(ToolInputError, match="Invalid repeat_until"):
            await _single(
                CronCreateTool(),
                {"schedule": "every 1h", "prompt": "hello", "repeat_until": "nope"},
                _ctx(tmp_path, manager),
            )

    async def test_cron_list_formats_empty_and_populated_results(self, tmp_path: Path) -> None:
        manager = _FakeScheduleManager()
        result = await _single(CronListTool(), {"include_completed": True}, _ctx(tmp_path, manager))

        assert manager.include_completed is True
        assert result.data["jobs"][0]["id"] == "job-1"
        assert "1 cron job(s)" in result.display.text
        assert "greeting" in result.display.text

        manager.tasks = []
        empty = await _single(CronListTool(), {}, _ctx(tmp_path, manager))

        assert empty.data == {"jobs": []}
        assert empty.display.text == "No cron jobs found."

    async def test_cron_list_requires_schedule_manager(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="Schedule subsystem"):
            await _single(CronListTool(), {}, _ctx(tmp_path))

    async def test_cron_delete_returns_success_and_not_found(self, tmp_path: Path) -> None:
        manager = _FakeScheduleManager()

        success = await _single(CronDeleteTool(), {"id": "job-1"}, _ctx(tmp_path, manager))
        missing = await _single(CronDeleteTool(), {"id": "missing"}, _ctx(tmp_path, manager))

        assert success.data == {"id": "job-1", "deleted": True}
        assert "Deleted cron job job-1" in success.display.text
        assert missing.data == {"id": "missing", "deleted": False}
        assert "not found" in missing.display.text
