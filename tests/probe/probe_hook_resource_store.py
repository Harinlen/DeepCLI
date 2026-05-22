"""Closure probe for ResourceStore-backed global hook declarations."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import orjson

from kernel.agents.mustang.hooks import AmbientContext, HookEvent, HookEventCtx, HookManager
from kernel.agents.mustang.hooks.config import HooksConfig
from kernel.agents.mustang.hooks.declarations import HookDeclarationStore
from kernel.agents.mustang.hooks.loader import _discover_layer
from kernel.agents.mustang.module_table import KernelModuleTable
from kernel.core.config import ConfigManager
from kernel.core.flags import FlagManager
from kernel.core.storage import ResourceStore


HANDLER_PLAINTEXT = "hook-handler-plaintext-must-not-enter-sqlite"
RUNTIME_MESSAGE = "hook-runtime-message-must-not-enter-sqlite"


def _write_hook(
    base: Path,
    name: str,
    description: str,
    *,
    events: tuple[str, ...] = ("stop",),
    message: str = RUNTIME_MESSAGE,
) -> None:
    hook_dir = base / name
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "HOOK.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"events: [{', '.join(events)}]\n"
        "---\n\n"
        f"# {name}\n",
        encoding="utf-8",
    )
    (hook_dir / "handler.py").write_text(
        "def handle(ctx):\n"
        f"    secret = {HANDLER_PLAINTEXT!r}\n"
        "    assert secret\n"
        f"    ctx.messages.append({message!r})\n",
        encoding="utf-8",
    )


async def _module_table(home: Path, *, cli_overrides: tuple[str, ...] = ()) -> KernelModuleTable:
    flags = FlagManager(resource_home=home)
    await flags.initialize()
    config = ConfigManager(resource_home=home, cli_overrides=cli_overrides)
    await config.startup()
    state_dir = home / "state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    return KernelModuleTable(flags=flags, config=config, state_dir=state_dir)


async def _start_manager(
    home: Path,
    *,
    user_dir: Path,
    project_dir: Path,
    cli_overrides: tuple[str, ...] = (),
) -> tuple[KernelModuleTable, HookManager]:
    mt = await _module_table(home, cli_overrides=cli_overrides)
    manager = HookManager(mt, user_hooks_dir=user_dir, project_hooks_dir=project_dir)
    await manager.startup()
    return mt, manager


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


async def _run() -> dict[str, object]:
    with TemporaryDirectory(prefix="mustang-hook-rs-") as tmp:
        home = Path(tmp)
        user_dir = home / "hooks"
        project_dir = home / "project-hooks"
        _write_hook(user_dir, "alpha", "Original global hook", message=RUNTIME_MESSAGE)

        mt, manager = await _start_manager(home, user_dir=user_dir, project_dir=project_dir)
        try:
            hook_startup_from_resource_store = [h.manifest.name for h in manager.loaded_hooks()] == [
                "alpha"
            ]
            legacy_import_once = (
                manager.declaration_import_report is not None
                and manager.declaration_import_report.imported == ("legacy:hooks.user_manifest",)
            )
            revision_after_import = mt.config.current_revisions()[
                "config.global._.hooks.global_declarations"
            ]
            ctx = _ctx(HookEvent.STOP)
            await manager.fire(ctx)
            fire_used_real_handler = ctx.messages == [RUNTIME_MESSAGE]
        finally:
            await manager.shutdown()
            mt.config.close()

        _write_hook(user_dir, "alpha", "Drifted filesystem hook", events=("session_end",))
        mt2, manager2 = await _start_manager(home, user_dir=user_dir, project_dir=project_dir)
        try:
            loaded = manager2.loaded_hooks()
            legacy_drift_ignored = (
                loaded[0].manifest.description == "Original global hook"
                and loaded[0].events == (HookEvent.STOP,)
                and manager2.declaration_import_report is not None
                and manager2.declaration_import_report.drift == ("legacy:hooks.user_manifest",)
            )
        finally:
            await manager2.shutdown()
            mt2.config.close()

        _write_hook(user_dir, "beta", "Beta hook", message="beta-fired")
        alpha, beta = _discover_layer(base_dir=user_dir, layer="user", opt_in=None)
        declarations = HookDeclarationStore.open(home)
        try:
            current = declarations.read_global()
            revision_after_add = declarations.write_global(
                [alpha, beta],
                expected_revision=current.revision if current else None,
                actor="probe",
            ).revision
            beta = replace(beta, manifest=replace(beta.manifest, description="Beta updated"))
            revision_after_update = declarations.write_global(
                [alpha, beta],
                expected_revision=revision_after_add,
                actor="probe",
            ).revision
            revision_after_delete = declarations.write_global(
                [beta],
                expected_revision=revision_after_update,
                actor="probe",
            ).revision
        finally:
            declarations.close()

        mt3, manager3 = await _start_manager(home, user_dir=user_dir, project_dir=project_dir)
        try:
            loaded = manager3.loaded_hooks()
            hook_reload_sees_resource_store_update = (
                [hook.manifest.name for hook in loaded] == ["beta"]
                and loaded[0].manifest.description == "Beta updated"
            )
        finally:
            await manager3.shutdown()
            mt3.config.close()

        _write_hook(project_dir, "project-one", "Project opt-in hook", message="project-fired")
        mt4, manager4 = await _start_manager(
            home,
            user_dir=user_dir,
            project_dir=project_dir,
            cli_overrides=("hooks.hooks.project_hooks={enabled: [project-one]}",),
        )
        try:
            section = mt4.config.get_section(file="hooks", section="hooks", schema=HooksConfig)
            cli_override_wins = section.get().project_hooks.enabled == ["project-one"]
            loaded_names = [hook.manifest.name for hook in manager4.loaded_hooks()]
            project_override_wins = "project-one" in loaded_names
        finally:
            await manager4.shutdown()
            mt4.config.close()

        store = ResourceStore.open(home)
        try:
            payload = store.read_tx(
                lambda conn: conn.execute(
                    "SELECT payload_json FROM config_sections "
                    "WHERE file = 'hooks' AND section = 'global_declarations'"
                ).fetchone()[0]
            )
            export_path = home / "export.json"
            store.export("json", export_path, dry_run=False)
            exported = orjson.dumps(orjson.loads(export_path.read_bytes())).decode()
        finally:
            store.close()

        handler_plaintext_leaked = HANDLER_PLAINTEXT in payload or HANDLER_PLAINTEXT in exported
        runtime_state_persisted = RUNTIME_MESSAGE in payload or RUNTIME_MESSAGE in exported

        return {
            "probe": "hook_resource_store",
            "hook_startup_from_resource_store": hook_startup_from_resource_store,
            "legacy_import_once": legacy_import_once,
            "legacy_drift_ignored": legacy_drift_ignored,
            "revision_after_import": revision_after_import,
            "revision_after_add": revision_after_add,
            "revision_after_update": revision_after_update,
            "revision_after_delete": revision_after_delete,
            "hook_reload_sees_resource_store_update": hook_reload_sees_resource_store_update,
            "fire_used_real_handler": fire_used_real_handler,
            "project_override_wins": project_override_wins,
            "cli_override_wins": cli_override_wins,
            "runtime_execution_state_persisted": runtime_state_persisted,
            "handler_plaintext_leaked": handler_plaintext_leaked,
            "result": "PASS",
        }


def main() -> None:
    result = asyncio.run(_run())
    failed = [
        key
        for key, value in result.items()
        if isinstance(value, bool)
        and key not in {"runtime_execution_state_persisted", "handler_plaintext_leaked"}
        and not value
    ]
    failed.extend(
        key
        for key in ("runtime_execution_state_persisted", "handler_plaintext_leaked")
        if result[key]
    )
    if failed:
        result["result"] = "FAIL"
    for key, value in result.items():
        print(f"{key}={value}")
    if failed:
        raise SystemExit(f"failed checks: {', '.join(failed)}")


if __name__ == "__main__":
    main()
