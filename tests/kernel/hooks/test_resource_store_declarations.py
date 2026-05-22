"""ResourceStore-backed global hook declaration tests."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import orjson
import pytest

from kernel.agents.mustang.hooks import AmbientContext, HookEvent, HookEventCtx, HookManager
from kernel.agents.mustang.hooks.declarations import HookDeclarationStore
from kernel.agents.mustang.hooks.loader import _discover_layer
from kernel.agents.mustang.module_table import KernelModuleTable
from kernel.core.config import ConfigManager
from kernel.core.config.sqlite_backend import ConfigSQLiteBackend
from kernel.core.flags import FlagManager
from kernel.core.storage import ResourceStore


def _write_hook(
    base: Path,
    name: str,
    description: str,
    *,
    events: tuple[str, ...] = ("stop",),
    handler_message: str = "hook fired",
    handler_plaintext: str = "handler-plaintext-should-not-be-in-resource-store",
) -> Path:
    hook_dir = base / name
    hook_dir.mkdir(parents=True, exist_ok=True)
    event_text = ", ".join(events)
    (hook_dir / "HOOK.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nevents: [{event_text}]\n---\n"
        f"\n# {name}\n",
        encoding="utf-8",
    )
    (hook_dir / "handler.py").write_text(
        "def handle(ctx):\n"
        f"    secret = {handler_plaintext!r}\n"
        "    assert secret\n"
        f"    ctx.messages.append({handler_message!r})\n",
        encoding="utf-8",
    )
    return hook_dir


async def _module_table(
    home: Path,
    *,
    cli_overrides: tuple[str, ...] = (),
) -> KernelModuleTable:
    flags = FlagManager(resource_home=home)
    await flags.initialize()
    config = ConfigManager(resource_home=home, cli_overrides=cli_overrides)
    await config.startup()
    state_dir = home / "state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    return KernelModuleTable(flags=flags, config=config, state_dir=state_dir)


def _ctx(event: HookEvent) -> HookEventCtx:
    return HookEventCtx(
        event=event,
        ambient=AmbientContext(
            session_id="s-1",
            cwd=Path.cwd(),
            agent_depth=0,
            mode="default",
            timestamp=time.time(),
        ),
    )


@pytest.mark.anyio
async def test_hook_declarations_startup_from_resource_store(tmp_path: Path) -> None:
    user_dir = tmp_path / "hooks"
    _write_hook(user_dir, "alpha", "ResourceStore alpha")

    mt = await _module_table(tmp_path)
    manager = HookManager(mt, user_hooks_dir=user_dir, project_hooks_dir=tmp_path / "project")
    await manager.startup()
    try:
        assert [hook.manifest.name for hook in manager.loaded_hooks()] == ["alpha"]
        assert manager.declaration_import_report is not None
        assert manager.declaration_import_report.imported == ("legacy:hooks.user_manifest",)
        assert mt.config.current_revisions()["config.global._.hooks.global_declarations"] == 1
        ctx = _ctx(HookEvent.STOP)
        assert await manager.fire(ctx) is False
        assert ctx.messages == ["hook fired"]
    finally:
        await manager.shutdown()
        mt.config.close()


@pytest.mark.anyio
async def test_legacy_hook_manifest_import_once_and_drift_ignored(tmp_path: Path) -> None:
    user_dir = tmp_path / "hooks"
    _write_hook(user_dir, "alpha", "Original declaration", events=("stop",))

    first = await _module_table(tmp_path)
    first_manager = HookManager(first, user_hooks_dir=user_dir, project_hooks_dir=tmp_path / "project")
    await first_manager.startup()
    await first_manager.shutdown()
    first.config.close()

    _write_hook(user_dir, "alpha", "Drifted declaration", events=("session_end",))
    second = await _module_table(tmp_path)
    second_manager = HookManager(
        second,
        user_hooks_dir=user_dir,
        project_hooks_dir=tmp_path / "project",
    )
    await second_manager.startup()
    try:
        loaded = second_manager.loaded_hooks()
        assert loaded[0].manifest.description == "Original declaration"
        assert loaded[0].events == (HookEvent.STOP,)
        assert second_manager.declaration_import_report is not None
        assert second_manager.declaration_import_report.drift == ("legacy:hooks.user_manifest",)
    finally:
        await second_manager.shutdown()
        second.config.close()


def test_hook_declaration_revision_bumps_on_add_update_delete(tmp_path: Path) -> None:
    user_dir = tmp_path / "hooks"
    _write_hook(user_dir, "alpha", "Alpha")
    _write_hook(user_dir, "beta", "Beta")
    alpha, beta = _discover_layer(base_dir=user_dir, layer="user", opt_in=None)

    declarations = HookDeclarationStore.open(tmp_path)
    try:
        one = declarations.write_global([alpha], expected_revision=None, actor="test")
        two = declarations.write_global([alpha, beta], expected_revision=one.revision, actor="test")
        beta = replace(beta, manifest=replace(beta.manifest, description="Beta updated"))
        three = declarations.write_global(
            [alpha, beta],
            expected_revision=two.revision,
            actor="test",
        )
        four = declarations.write_global([beta], expected_revision=three.revision, actor="test")

        assert (one.revision, two.revision, three.revision, four.revision) == (1, 2, 3, 4)
    finally:
        declarations.close()


@pytest.mark.anyio
async def test_project_hook_opt_in_config_still_wins(tmp_path: Path) -> None:
    project_dir = tmp_path / "project-hooks"
    _write_hook(project_dir, "project-one", "Project hook", handler_message="project fired")
    store = ResourceStore.open(tmp_path)
    try:
        ConfigSQLiteBackend(store).write(
            file="hooks",
            section="hooks",
            payload={"project_hooks": {"enabled": ["project-one"]}},
            expected_revision=None,
            actor="test",
        )
    finally:
        store.close()

    mt = await _module_table(tmp_path)
    manager = HookManager(mt, user_hooks_dir=tmp_path / "user-hooks", project_hooks_dir=project_dir)
    await manager.startup()
    try:
        assert [hook.manifest.name for hook in manager.loaded_hooks()] == ["project-one"]
        ctx = _ctx(HookEvent.STOP)
        assert await manager.fire(ctx) is False
        assert ctx.messages == ["project fired"]
    finally:
        await manager.shutdown()
        mt.config.close()


@pytest.mark.anyio
async def test_hook_declarations_do_not_persist_handler_or_runtime_state(tmp_path: Path) -> None:
    handler_plaintext = "handler-plaintext-should-not-be-in-resource-store"
    runtime_message = "runtime-message-should-not-be-in-resource-store"
    user_dir = tmp_path / "hooks"
    _write_hook(
        user_dir,
        "alpha",
        "Alpha",
        handler_message=runtime_message,
        handler_plaintext=handler_plaintext,
    )

    mt = await _module_table(tmp_path)
    manager = HookManager(mt, user_hooks_dir=user_dir, project_hooks_dir=tmp_path / "project")
    await manager.startup()
    try:
        ctx = _ctx(HookEvent.STOP)
        assert await manager.fire(ctx) is False
        assert ctx.messages == [runtime_message]
        store = ResourceStore.open(tmp_path)
        try:
            payload = store.read_tx(
                lambda conn: conn.execute(
                    "SELECT payload_json FROM config_sections "
                    "WHERE file = 'hooks' AND section = 'global_declarations'"
                ).fetchone()[0]
            )
            export_path = tmp_path / "export.json"
            store.export("json", export_path, dry_run=False)
            exported = orjson.dumps(orjson.loads(export_path.read_bytes())).decode()
        finally:
            store.close()

        assert handler_plaintext not in payload
        assert runtime_message not in payload
        assert handler_plaintext not in exported
        assert runtime_message not in exported
    finally:
        await manager.shutdown()
        mt.config.close()
