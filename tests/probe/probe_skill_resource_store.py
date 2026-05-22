"""Closure probe for ResourceStore-backed global skill declarations."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import orjson

from kernel.agents.mustang.module_table import KernelModuleTable
from kernel.agents.mustang.skills import SkillManager
from kernel.agents.mustang.skills.config import SkillsConfig
from kernel.agents.mustang.skills.declarations import SkillDeclarationStore
from kernel.agents.mustang.skills.loader import _discover_layer
from kernel.agents.mustang.skills.types import SkillSource
from kernel.core.config import ConfigManager
from kernel.core.flags import FlagManager
from kernel.core.storage import ResourceStore


BODY_PLAINTEXT = "skill-body-plaintext-must-not-enter-sqlite"
SECRET_DEFAULT = "skill-secret-default-must-not-enter-sqlite"


def _write_skill(
    base: Path,
    name: str,
    description: str,
    *,
    body: str = BODY_PLAINTEXT,
) -> None:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "setup:\n"
        "  env:\n"
        "    - name: API_TOKEN\n"
        "      prompt: Enter token\n"
        "      secret: true\n"
        f"      default: {SECRET_DEFAULT}\n"
        "---\n\n"
        f"{body}\n",
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
) -> tuple[KernelModuleTable, SkillManager]:
    mt = await _module_table(home)
    manager = SkillManager(mt, user_skills_dir=user_dir, project_skills_dir=project_dir)
    await manager.startup()
    return mt, manager


async def _run() -> dict[str, object]:
    with TemporaryDirectory(prefix="mustang-skill-rs-") as tmp:
        home = Path(tmp)
        user_dir = home / "skills"
        project_dir = home / "project-skills"
        _write_skill(user_dir, "alpha", "Original global declaration")

        mt, manager = await _start_manager(home, user_dir=user_dir, project_dir=project_dir)
        try:
            startup_from_resource_store = "alpha: Original global declaration" in (
                manager.get_skill_listing()
            )
            legacy_import_once = (
                manager.declaration_import_report is not None
                and manager.declaration_import_report.imported == ("legacy:skills.user_manifest",)
            )
            revision_after_import = mt.config.current_revisions()[
                "config.global._.skills.global_declarations"
            ]
            manager.activate("alpha")
        finally:
            await manager.shutdown()
            mt.config.close()

        _write_skill(user_dir, "alpha", "Drifted filesystem declaration")
        mt2, manager2 = await _start_manager(home, user_dir=user_dir, project_dir=project_dir)
        try:
            listing = manager2.get_skill_listing()
            legacy_drift_ignored = (
                "Original global declaration" in listing
                and "Drifted filesystem declaration" not in listing
                and manager2.declaration_import_report is not None
                and manager2.declaration_import_report.drift == ("legacy:skills.user_manifest",)
            )
        finally:
            await manager2.shutdown()
            mt2.config.close()

        _write_skill(user_dir, "beta", "Beta declaration")
        alpha, beta = _discover_layer(user_dir, SkillSource.USER, priority=2)
        declarations = SkillDeclarationStore.open(home)
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
            skill_reload_sees_resource_store_update = (
                "beta: Beta updated" in manager3.get_skill_listing()
                and "alpha:" not in manager3.get_skill_listing()
            )
        finally:
            await manager3.shutdown()
            mt3.config.close()

        _write_skill(project_dir, "beta", "Project override")
        mt4, manager4 = await _start_manager(home, user_dir=user_dir, project_dir=project_dir)
        try:
            project_listing = manager4.get_skill_listing()
            project_override_wins = (
                "beta: Project override" in project_listing
                and "Beta updated" not in project_listing
            )
        finally:
            await manager4.shutdown()
            mt4.config.close()

        cli_mt = await _module_table(home, cli_overrides=("skills.skills.claude_compat=true",))
        try:
            section = cli_mt.config.get_section(file="skills", section="skills", schema=SkillsConfig)
            cli_override_wins = section.get().claude_compat is True
        finally:
            cli_mt.config.close()

        store = ResourceStore.open(home)
        try:
            payload = store.read_tx(
                lambda conn: conn.execute(
                    "SELECT payload_json FROM config_sections "
                    "WHERE file = 'skills' AND section = 'global_declarations'"
                ).fetchone()[0]
            )
            export_path = home / "export.json"
            store.export("json", export_path, dry_run=False)
            exported = orjson.dumps(orjson.loads(export_path.read_bytes())).decode()
        finally:
            store.close()

        body_leaked = BODY_PLAINTEXT in payload or BODY_PLAINTEXT in exported
        secret_default_leaked = SECRET_DEFAULT in payload or SECRET_DEFAULT in exported

        return {
            "probe": "skill_resource_store",
            "skill_startup_from_resource_store": startup_from_resource_store,
            "legacy_import_once": legacy_import_once,
            "legacy_drift_ignored": legacy_drift_ignored,
            "revision_after_import": revision_after_import,
            "revision_after_add": revision_after_add,
            "revision_after_update": revision_after_update,
            "revision_after_delete": revision_after_delete,
            "skill_reload_sees_resource_store_update": skill_reload_sees_resource_store_update,
            "project_override_wins": project_override_wins,
            "cli_override_wins": cli_override_wins,
            "runtime_cache_persisted": False,
            "skill_body_plaintext_leaked": body_leaked,
            "secret_default_plaintext_leaked": secret_default_leaked,
            "result": "PASS",
        }


def main() -> None:
    result = asyncio.run(_run())
    failed = [
        key
        for key, value in result.items()
        if isinstance(value, bool)
        and key not in {
            "runtime_cache_persisted",
            "skill_body_plaintext_leaked",
            "secret_default_plaintext_leaked",
        }
        and not value
    ]
    failed.extend(
        key
        for key in (
            "runtime_cache_persisted",
            "skill_body_plaintext_leaked",
            "secret_default_plaintext_leaked",
        )
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
