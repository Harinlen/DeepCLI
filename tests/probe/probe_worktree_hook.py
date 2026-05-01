"""Live probe: EnterWorktree hook-based fallback for non-git projects.

Constructs a real HookManager with a temp user_hooks_dir that contains a
WorktreeCreate hook.  Builds an EnterWorktreeTool, wires a ToolContext
whose ``fire_hook`` closure bridges to the HookManager, and invokes the
tool from a non-git cwd.  Verifies the hook handler runs, sets
``worktree_handled``, and the tool returns the hook-provided path.

Exit 0 on success, 1 on failure.

Run:

    cd /home/saki/Documents/truenorth/mustang
    .venv/bin/python scripts/probe_worktree_hook.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "kernel"))


HOOK_MANIFEST = """\
---
name: probe-worktree-create
description: Probe hook that materialises a worktree directory under /tmp.
events: [worktree_create, worktree_remove]
---

# Probe WorktreeCreate / WorktreeRemove

This hook exists purely to validate that EnterWorktree and ExitWorktree
dispatch to the hook path when git is unavailable.  On CREATE it mkdirs
a directory under the probe's temp root and writes a marker file; on
REMOVE it acknowledges.
"""

HOOK_HANDLER = '''\
"""handler.py — WorktreeCreate/Remove probe handler."""

from pathlib import Path


async def handle(ctx):
    """Echo-style handler: materialises a fake worktree and marks it handled.

    CREATE: mkdir ``<probe_root>/<slug>`` and write marker.
    REMOVE: just set worktree_handled=True.
    """
    import os
    probe_root = Path(os.environ.get("MUSTANG_PROBE_WT_ROOT", "/tmp/probe-wt"))

    # HookEvent is imported lazily so this file can be imported by the
    # kernel's loader without dragging our test modules.
    from kernel.hooks.types import HookEvent

    if ctx.event is HookEvent.WORKTREE_CREATE:
        slug = ctx.worktree_slug or "unnamed"
        target = probe_root / slug
        target.mkdir(parents=True, exist_ok=True)
        (target / ".probe-created-by-hook").write_text(slug, encoding="utf-8")
        ctx.worktree_path = str(target)
        ctx.worktree_handled = True
        return

    if ctx.event is HookEvent.WORKTREE_REMOVE:
        ctx.worktree_handled = True
        return
'''


async def run() -> int:
    import os
    import shutil

    from kernel.config import ConfigManager
    from kernel.flags import FlagManager
    from kernel.hooks import HookManager
    from kernel.module_table import KernelModuleTable
    from kernel.prompts.manager import PromptManager
    from kernel.tools.builtin.enter_worktree import EnterWorktreeTool
    from kernel.tools.builtin.exit_worktree import ExitWorktreeTool
    from kernel.tools.context import ToolContext
    from kernel.tools.file_state import FileStateCache
    import logging

    logging.basicConfig(level=logging.WARNING)

    # 1. Build a temp workspace: non-git cwd + user_hooks_dir with the
    #    probe hook laid out as a discoverable hook directory.
    scratch = Path("/tmp/probe-worktree")
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir()

    non_git_cwd = scratch / "non-git-project"
    non_git_cwd.mkdir()

    hooks_root = scratch / "hooks"
    hook_dir = hooks_root / "probe-worktree-create"
    hook_dir.mkdir(parents=True)
    (hook_dir / "HOOK.md").write_text(HOOK_MANIFEST, encoding="utf-8")
    (hook_dir / "handler.py").write_text(HOOK_HANDLER, encoding="utf-8")

    probe_wt_root = scratch / "wt-storage"
    os.environ["MUSTANG_PROBE_WT_ROOT"] = str(probe_wt_root)

    # 2. Boot the minimum kernel: config + flags + prompts + hooks.
    #    We do *not* need LLMManager for this probe — no summarisation.
    state_dir = scratch / "state"
    state_dir.mkdir(mode=0o700)

    flags_path = scratch / "flags.yaml"
    flags = FlagManager(path=flags_path)
    await flags.initialize()

    config_global = scratch / "config"
    config_global.mkdir()
    config_project = non_git_cwd / ".mustang" / "config"
    config_project.mkdir(parents=True)
    config = ConfigManager(
        global_dir=config_global,
        project_dir=config_project,
        cli_overrides=(),
    )
    await config.startup()

    prompts = PromptManager()
    prompts.load()

    mt = KernelModuleTable(
        flags=flags, config=config, state_dir=state_dir, prompts=prompts
    )

    # 3. Start HookManager pointed at our probe hooks_root.
    hooks = HookManager(mt, user_hooks_dir=hooks_root, project_hooks_dir=None)
    await hooks.startup()
    mt.register(hooks)

    loaded = list(hooks._registry._handlers.keys())  # type: ignore[attr-defined]
    print(f"HookManager loaded events: {[e.value for e in loaded]}")

    # 4. Construct a ToolContext whose fire_hook closure bridges to the
    #    live HookManager (same shape as ToolExecutor wiring).
    async def _fire_hook(event, event_ctx) -> bool:
        return await hooks.fire(event_ctx)

    ctx = ToolContext(
        session_id="probe-wt",
        agent_depth=0,
        agent_id=None,
        cwd=non_git_cwd,
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
        git_manager=None,  # ← triggers the hook fallback path
        fire_hook=_fire_hook,
    )

    # 5. Call EnterWorktree.
    tool = EnterWorktreeTool()
    enter_result = None
    async for ev in tool.call({"slug": "probe-feature"}, ctx):
        enter_result = ev

    assert enter_result is not None, "EnterWorktree emitted no events"
    data = enter_result.data
    print(f"EnterWorktree data: {data}")
    assert data.get("backend") == "hook", (
        f"Expected backend=hook, got {data.get('backend')!r}"
    )
    assert "probe-feature" in data["worktree_path"], data

    # Verify the hook handler actually ran on disk.
    marker = probe_wt_root / "probe-feature" / ".probe-created-by-hook"
    assert marker.exists(), f"Hook did not create marker at {marker}"
    assert marker.read_text() == "probe-feature"
    print(f"Hook marker on disk: {marker} ✓")

    # 6. Call ExitWorktree from the hook-created directory.
    exit_tool = ExitWorktreeTool()
    # Simulate: cwd is now the hook-created dir (as if context_modifier
    # had run in a real session loop).
    ctx.cwd = Path(data["worktree_path"])
    exit_result = None
    async for ev in exit_tool.call({"action": "remove"}, ctx):
        exit_result = ev

    assert exit_result is not None, "ExitWorktree emitted no events"
    exit_data = exit_result.data
    print(f"ExitWorktree data: {exit_data}")
    assert exit_data.get("backend") == "hook", exit_data
    assert exit_data.get("action") == "remove", exit_data

    print("\nOK: WORKTREE_CREATE and WORKTREE_REMOVE hooks fired end-to-end.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
