"""Tests for Orchestrator.set_permission_mode (Step 5.8)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from daemon.engine.orchestrator import Orchestrator
from daemon.engine.stream import PermissionModeChanged, PlanModeChanged
from daemon.permissions.modes import PermissionMode
from daemon.providers.base import Message, ModelInfo, Provider


class _FakeProvider(Provider):
    name = "fake"

    async def stream(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
        if False:
            yield None  # type: ignore[unreachable]

    async def models(self) -> list[ModelInfo]:
        return []


def _make_orch(tmp_path: Path) -> Orchestrator:
    from tests.daemon.engine.orchestrator_test_utils import make_test_orchestrator

    return make_test_orchestrator(
        provider=_FakeProvider(),
        tmp_path=tmp_path,
        session_dir=tmp_path,
        session_id="test-session",
    )


async def _drain(orch: Orchestrator, target: PermissionMode) -> list[Any]:
    return [evt async for evt in orch.set_permission_mode(target)]


@pytest.mark.asyncio
async def test_switch_to_accept_edits(tmp_path: Path) -> None:
    orch = _make_orch(tmp_path)
    assert orch.permission_engine.mode == PermissionMode.PROMPT

    events = await _drain(orch, PermissionMode.ACCEPT_EDITS)

    assert orch.permission_engine.mode == PermissionMode.ACCEPT_EDITS
    assert any(
        isinstance(e, PermissionModeChanged)
        and e.mode == "accept_edits"
        and e.previous_mode == "default"
        for e in events
    )


@pytest.mark.asyncio
async def test_noop_when_already_in_target_mode(tmp_path: Path) -> None:
    orch = _make_orch(tmp_path)
    events = await _drain(orch, PermissionMode.PROMPT)
    assert events == []


@pytest.mark.asyncio
async def test_enter_plan_via_set_permission_mode(tmp_path: Path) -> None:
    orch = _make_orch(tmp_path)
    events = await _drain(orch, PermissionMode.PLAN)

    assert orch.permission_engine.mode == PermissionMode.PLAN
    # Delegates to enter_plan_mode — should emit both a PlanModeChanged
    # and a PermissionModeChanged.
    assert any(isinstance(e, PlanModeChanged) and e.active for e in events)
    assert any(isinstance(e, PermissionModeChanged) and e.mode == "plan" for e in events)


@pytest.mark.asyncio
async def test_exit_plan_to_another_mode(tmp_path: Path) -> None:
    orch = _make_orch(tmp_path)
    # Enter plan first.
    await _drain(orch, PermissionMode.PLAN)
    assert orch.permission_engine.mode == PermissionMode.PLAN

    events = await _drain(orch, PermissionMode.ACCEPT_EDITS)

    assert orch.permission_engine.mode == PermissionMode.ACCEPT_EDITS
    # Should emit PlanModeChanged(active=False) and a
    # PermissionModeChanged to the new target.
    assert any(isinstance(e, PlanModeChanged) and not e.active for e in events)
    assert any(isinstance(e, PermissionModeChanged) and e.mode == "accept_edits" for e in events)


# -- Sanity: a Message round-trip through the orchestrator is untouched --


def test_message_user_with_no_images() -> None:
    m = Message.user("hi")
    assert len(m.content) == 1
