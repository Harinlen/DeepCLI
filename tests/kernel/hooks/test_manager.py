"""HookManager subsystem — startup, fire semantics, integration with discovery."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from kernel.config import ConfigManager
from kernel.flags import FlagManager
from kernel.hooks import (
    AmbientContext,
    HookBlock,
    HookEvent,
    HookEventCtx,
    HookManager,
)
from kernel.hooks.registry import HookRegistry
from kernel.hooks.types import EVENT_SPECS
from kernel.module_table import KernelModuleTable


@pytest.fixture
async def module_table(tmp_path: Path) -> KernelModuleTable:
    flags = FlagManager(path=tmp_path / "flags.yaml")
    await flags.initialize()

    config = ConfigManager(
        global_dir=tmp_path / "config",
        project_dir=tmp_path / "project-config",
        cli_overrides=(),
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "project-config").mkdir()
    await config.startup()

    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    return KernelModuleTable(flags=flags, config=config, state_dir=state_dir)


def _make_ctx(event: HookEvent, **fields) -> HookEventCtx:  # type: ignore[no-untyped-def]
    """Build a HookEventCtx with sensible default ambient state."""
    ambient = AmbientContext(
        session_id="s-1",
        cwd=Path.cwd(),
        agent_depth=0,
        mode="default",
        timestamp=time.time(),
    )
    return HookEventCtx(event=event, ambient=ambient, **fields)


@pytest.mark.anyio
async def test_startup_with_no_hook_dirs_is_noop(
    module_table: KernelModuleTable, tmp_path: Path
) -> None:
    mgr = HookManager(
        module_table,
        user_hooks_dir=tmp_path / "absent-user",
        project_hooks_dir=tmp_path / "absent-project",
    )
    await mgr.startup()
    assert mgr.loaded_hooks() == ()
    # fire on empty registry returns False (no block).
    blocked = await mgr.fire(_make_ctx(HookEvent.STOP))
    assert blocked is False
    await mgr.shutdown()


@pytest.mark.anyio
async def test_handler_can_mutate_user_text(
    module_table: KernelModuleTable, tmp_path: Path
) -> None:
    mgr = HookManager(module_table)
    # Skip discovery; register directly via internal API.
    await mgr.startup()

    async def rewrite(ctx: HookEventCtx) -> None:
        ctx.user_text = "rewritten"

    _registry(mgr).register(HookEvent.USER_PROMPT_SUBMIT, rewrite)

    ctx = _make_ctx(HookEvent.USER_PROMPT_SUBMIT, user_text="original")
    blocked = await mgr.fire(ctx)
    assert blocked is False
    assert ctx.user_text == "rewritten"


@pytest.mark.anyio
async def test_handler_can_append_messages(
    module_table: KernelModuleTable,
) -> None:
    mgr = HookManager(module_table)
    await mgr.startup()

    async def reminder(ctx: HookEventCtx) -> None:
        ctx.messages.append("hello from hook")

    _registry(mgr).register(HookEvent.STOP, reminder)

    ctx = _make_ctx(HookEvent.STOP)
    await mgr.fire(ctx)
    assert ctx.messages == ["hello from hook"]


@pytest.mark.anyio
async def test_hook_block_aborts_blockable_event(
    module_table: KernelModuleTable,
) -> None:
    mgr = HookManager(module_table)
    await mgr.startup()

    calls: list[str] = []

    async def first(ctx: HookEventCtx) -> None:
        calls.append("first")
        raise HookBlock("nope")

    async def second(ctx: HookEventCtx) -> None:
        calls.append("second")

    _registry(mgr).register(HookEvent.PRE_TOOL_USE, first)
    _registry(mgr).register(HookEvent.PRE_TOOL_USE, second)

    blocked = await mgr.fire(_make_ctx(HookEvent.PRE_TOOL_USE))
    assert blocked is True
    # Second handler must not run after the block fires.
    assert calls == ["first"]


@pytest.mark.anyio
async def test_hook_block_ignored_on_non_blocking_event(
    module_table: KernelModuleTable, caplog: pytest.LogCaptureFixture
) -> None:
    mgr = HookManager(module_table)
    await mgr.startup()

    calls: list[str] = []

    async def block_attempt(ctx: HookEventCtx) -> None:
        calls.append("first")
        raise HookBlock("trying to block")

    async def second(ctx: HookEventCtx) -> None:
        calls.append("second")

    # POST_TOOL_USE has can_block=False — HookBlock must be logged + ignored
    # AND the second handler must still run.
    assert EVENT_SPECS[HookEvent.POST_TOOL_USE].can_block is False
    _registry(mgr).register(HookEvent.POST_TOOL_USE, block_attempt)
    _registry(mgr).register(HookEvent.POST_TOOL_USE, second)

    blocked = await mgr.fire(_make_ctx(HookEvent.POST_TOOL_USE))
    assert blocked is False
    assert calls == ["first", "second"]


@pytest.mark.anyio
async def test_handler_exception_fails_open(
    module_table: KernelModuleTable, caplog: pytest.LogCaptureFixture
) -> None:
    mgr = HookManager(module_table)
    await mgr.startup()

    calls: list[str] = []

    async def crashing(ctx: HookEventCtx) -> None:
        calls.append("crashing")
        raise RuntimeError("oops")

    async def survivor(ctx: HookEventCtx) -> None:
        calls.append("survivor")

    _registry(mgr).register(HookEvent.STOP, crashing)
    _registry(mgr).register(HookEvent.STOP, survivor)

    blocked = await mgr.fire(_make_ctx(HookEvent.STOP))
    assert blocked is False
    # Survivor must run after the crash.
    assert calls == ["crashing", "survivor"]


@pytest.mark.anyio
async def test_sync_handler_runs(
    module_table: KernelModuleTable,
) -> None:
    """Plain ``def handle`` must work — no ``async`` keyword required."""
    mgr = HookManager(module_table)
    await mgr.startup()

    calls: list[str] = []

    def sync_handler(ctx: HookEventCtx) -> None:
        calls.append("sync")
        ctx.messages.append("sync said hi")

    _registry(mgr).register(HookEvent.STOP, sync_handler)
    ctx = _make_ctx(HookEvent.STOP)
    await mgr.fire(ctx)
    assert calls == ["sync"]
    assert ctx.messages == ["sync said hi"]


@pytest.mark.anyio
async def test_handlers_run_sequentially_in_registration_order(
    module_table: KernelModuleTable,
) -> None:
    mgr = HookManager(module_table)
    await mgr.startup()

    order: list[int] = []

    def make(i: int):  # type: ignore[no-untyped-def]
        async def h(ctx: HookEventCtx) -> None:
            order.append(i)

        return h

    for i in range(5):
        _registry(mgr).register(HookEvent.STOP, make(i))

    await mgr.fire(_make_ctx(HookEvent.STOP))
    assert order == [0, 1, 2, 3, 4]


@pytest.mark.anyio
async def test_shutdown_clears_registry(
    module_table: KernelModuleTable,
) -> None:
    mgr = HookManager(module_table)
    await mgr.startup()

    async def h(ctx: HookEventCtx) -> None:
        pass

    _registry(mgr).register(HookEvent.STOP, h)
    assert len(_registry(mgr)) == 1

    await mgr.shutdown()
    assert len(_registry(mgr)) == 0
    assert mgr.loaded_hooks() == ()


# Helper to access the private registry — avoids exposing it as public
# API just for tests.
def _registry(mgr: HookManager) -> HookRegistry:
    return mgr._registry  # noqa: SLF001  (test introspection is intentional)
