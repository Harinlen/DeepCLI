"""End-to-end: HOOK.md on disk -> HookManager.startup -> fire mutates ctx."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from kernel.config import ConfigManager
from kernel.flags import FlagManager
from kernel.hooks import (
    AmbientContext,
    HookEvent,
    HookEventCtx,
    HookManager,
)
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


def _write_hook_dir(
    base: Path,
    name: str,
    *,
    events: list[str],
    handler_body: str,
) -> Path:
    hook_dir = base / name
    hook_dir.mkdir(parents=True)
    (hook_dir / "HOOK.md").write_text(
        f"---\nname: {name}\nevents: {events}\n---\n",
        encoding="utf-8",
    )
    (hook_dir / "handler.py").write_text(handler_body, encoding="utf-8")
    return hook_dir


def _ctx(event: HookEvent, **fields) -> HookEventCtx:  # type: ignore[no-untyped-def]
    ambient = AmbientContext(
        session_id="s-1",
        cwd=Path.cwd(),
        agent_depth=0,
        mode="default",
        timestamp=time.time(),
    )
    return HookEventCtx(event=event, ambient=ambient, **fields)


@pytest.mark.anyio
async def test_user_hook_loads_and_fires_end_to_end(
    module_table: KernelModuleTable, tmp_path: Path
) -> None:
    """Write a HOOK.md to disk, start HookManager, fire, observe mutation."""
    user = tmp_path / "user-hooks"
    _write_hook_dir(
        user,
        "reminder",
        events=["stop"],
        handler_body=(
            "async def handle(ctx):\n"
            "    ctx.messages.append(f'session={ctx.ambient.session_id}')\n"
        ),
    )

    mgr = HookManager(
        module_table,
        user_hooks_dir=user,
        project_hooks_dir=tmp_path / "absent-project",
    )
    await mgr.startup()

    loaded = mgr.loaded_hooks()
    assert len(loaded) == 1
    assert loaded[0].manifest.name == "reminder"
    assert loaded[0].layer == "user"

    ctx = _ctx(HookEvent.STOP)
    blocked = await mgr.fire(ctx)
    assert blocked is False
    assert ctx.messages == ["session=s-1"]

    await mgr.shutdown()


@pytest.mark.anyio
async def test_project_hook_requires_explicit_opt_in(
    module_table: KernelModuleTable, tmp_path: Path
) -> None:
    """Project-layer hook must be listed in hooks.yaml to be loaded."""
    user = tmp_path / "user-hooks"
    user.mkdir()  # empty
    project = tmp_path / "project-hooks"
    _write_hook_dir(
        project,
        "proj-only",
        events=["stop"],
        handler_body="async def handle(ctx):\n    ctx.messages.append('proj')\n",
    )

    # 1) Without opt-in: hook does not load.
    mgr = HookManager(
        module_table,
        user_hooks_dir=user,
        project_hooks_dir=project,
    )
    await mgr.startup()
    assert mgr.loaded_hooks() == ()
    await mgr.shutdown()

    # 2) With opt-in via ConfigManager: hook loads.
    # Write hooks.yaml under the global config dir before startup.
    hooks_yaml = tmp_path / "config" / "hooks.yaml"
    hooks_yaml.write_text(
        yaml.safe_dump({"hooks": {"project_hooks": {"enabled": ["proj-only"]}}}),
        encoding="utf-8",
    )

    # Rebuild a fresh ConfigManager so it re-reads hooks.yaml.
    flags = FlagManager(path=tmp_path / "flags.yaml")
    await flags.initialize()
    config = ConfigManager(
        global_dir=tmp_path / "config",
        project_dir=tmp_path / "project-config",
        cli_overrides=(),
    )
    await config.startup()
    state_dir = tmp_path / "state2"
    state_dir.mkdir(mode=0o700)
    mt2 = KernelModuleTable(flags=flags, config=config, state_dir=state_dir)

    mgr2 = HookManager(
        mt2,
        user_hooks_dir=user,
        project_hooks_dir=project,
    )
    await mgr2.startup()
    assert len(mgr2.loaded_hooks()) == 1
    assert mgr2.loaded_hooks()[0].layer == "project"

    ctx = _ctx(HookEvent.STOP)
    await mgr2.fire(ctx)
    assert ctx.messages == ["proj"]
    await mgr2.shutdown()


@pytest.mark.anyio
async def test_multi_event_hook_registers_for_each_event(
    module_table: KernelModuleTable, tmp_path: Path
) -> None:
    """Single handler subscribes to multiple events via frontmatter list."""
    user = tmp_path / "user-hooks"
    _write_hook_dir(
        user,
        "multi",
        events=["pre_tool_use", "stop"],
        handler_body=(
            "async def handle(ctx):\n"
            "    ctx.messages.append(ctx.event.value)\n"
        ),
    )

    mgr = HookManager(
        module_table,
        user_hooks_dir=user,
        project_hooks_dir=tmp_path / "absent",
    )
    await mgr.startup()

    ctx_a = _ctx(HookEvent.PRE_TOOL_USE, tool_name="Bash")
    await mgr.fire(ctx_a)
    assert ctx_a.messages == ["pre_tool_use"]

    ctx_b = _ctx(HookEvent.STOP)
    await mgr.fire(ctx_b)
    assert ctx_b.messages == ["stop"]

    await mgr.shutdown()


@pytest.mark.anyio
async def test_one_bad_hook_does_not_break_others(
    module_table: KernelModuleTable, tmp_path: Path
) -> None:
    """Loader skips broken hooks; sibling hooks still load."""
    user = tmp_path / "user-hooks"
    user.mkdir()

    # Bad: malformed manifest.
    bad = user / "broken"
    bad.mkdir()
    (bad / "HOOK.md").write_text("not a frontmatter\n")
    (bad / "handler.py").write_text("async def handle(ctx): pass\n")

    # Good: standard hook.
    _write_hook_dir(
        user,
        "good",
        events=["stop"],
        handler_body="async def handle(ctx):\n    ctx.messages.append('ok')\n",
    )

    mgr = HookManager(
        module_table,
        user_hooks_dir=user,
        project_hooks_dir=tmp_path / "absent",
    )
    await mgr.startup()

    loaded = mgr.loaded_hooks()
    assert len(loaded) == 1
    assert loaded[0].manifest.name == "good"
