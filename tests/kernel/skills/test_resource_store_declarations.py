"""ResourceStore-backed global skill declaration tests."""

from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from kernel.agents.mustang.module_table import KernelModuleTable
from kernel.agents.mustang.skills import SkillManager
from kernel.agents.mustang.skills.declarations import SkillDeclarationStore
from kernel.agents.mustang.skills.loader import _discover_layer
from kernel.agents.mustang.skills.types import SkillSource
from kernel.core.config import ConfigManager
from kernel.core.flags import FlagManager
from kernel.core.storage import ResourceStore


def _write_skill(
    base: Path,
    name: str,
    description: str,
    *,
    body: str = "Skill body",
    setup_secret_default: str | None = None,
) -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    setup = ""
    if setup_secret_default is not None:
        setup = (
            "setup:\n"
            "  env:\n"
            "    - name: API_TOKEN\n"
            "      prompt: Enter token\n"
            "      secret: true\n"
            f"      default: {setup_secret_default}\n"
        )
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{setup}---\n\n{body}\n",
        encoding="utf-8",
    )
    return skill_dir


async def _module_table(home: Path) -> KernelModuleTable:
    flags = FlagManager(resource_home=home)
    await flags.initialize()
    config = ConfigManager(resource_home=home)
    await config.startup()
    state_dir = home / "state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    return KernelModuleTable(flags=flags, config=config, state_dir=state_dir)


@pytest.mark.anyio
async def test_skill_declarations_startup_from_resource_store(tmp_path: Path) -> None:
    user_dir = tmp_path / "skills"
    _write_skill(user_dir, "alpha", "ResourceStore alpha")

    mt = await _module_table(tmp_path)
    manager = SkillManager(mt, user_skills_dir=user_dir, project_skills_dir=tmp_path / "project")
    await manager.startup()
    try:
        assert "alpha: ResourceStore alpha" in manager.get_skill_listing()
        assert manager.declaration_import_report is not None
        assert manager.declaration_import_report.imported == ("legacy:skills.user_manifest",)
        assert mt.config.current_revisions()["config.global._.skills.global_declarations"] == 1
    finally:
        await manager.shutdown()
        mt.config.close()


@pytest.mark.anyio
async def test_legacy_skill_manifest_import_once_and_drift_ignored(tmp_path: Path) -> None:
    user_dir = tmp_path / "skills"
    _write_skill(user_dir, "alpha", "Original declaration")

    first = await _module_table(tmp_path)
    first_manager = SkillManager(
        first,
        user_skills_dir=user_dir,
        project_skills_dir=tmp_path / "project",
    )
    await first_manager.startup()
    await first_manager.shutdown()
    first.config.close()

    _write_skill(user_dir, "alpha", "Drifted filesystem declaration")
    second = await _module_table(tmp_path)
    second_manager = SkillManager(
        second,
        user_skills_dir=user_dir,
        project_skills_dir=tmp_path / "project",
    )
    await second_manager.startup()
    try:
        assert "alpha: Original declaration" in second_manager.get_skill_listing()
        assert "Drifted filesystem" not in second_manager.get_skill_listing()
        assert second_manager.declaration_import_report is not None
        assert second_manager.declaration_import_report.drift == ("legacy:skills.user_manifest",)
    finally:
        await second_manager.shutdown()
        second.config.close()


def test_skill_declaration_revision_bumps_on_add_update_delete(tmp_path: Path) -> None:
    user_dir = tmp_path / "skills"
    _write_skill(user_dir, "alpha", "Alpha")
    _write_skill(user_dir, "beta", "Beta")
    alpha, beta = _discover_layer(user_dir, SkillSource.USER, priority=2)

    declarations = SkillDeclarationStore.open(tmp_path)
    try:
        one = declarations.write_global([alpha], expected_revision=None, actor="test")
        two = declarations.write_global([alpha, beta], expected_revision=one.revision, actor="test")
        beta.manifest = beta.manifest.__class__(
            **{**beta.manifest.__dict__, "description": "Beta updated"}
        )
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
async def test_project_skill_override_still_wins(tmp_path: Path) -> None:
    user_dir = tmp_path / "skills"
    project_dir = tmp_path / "project-skills"
    _write_skill(user_dir, "shared", "Global declaration")
    _write_skill(project_dir, "shared", "Project override")

    mt = await _module_table(tmp_path)
    manager = SkillManager(mt, user_skills_dir=user_dir, project_skills_dir=project_dir)
    await manager.startup()
    try:
        listing = manager.get_skill_listing()
        assert "shared: Project override" in listing
        assert "Global declaration" not in listing
    finally:
        await manager.shutdown()
        mt.config.close()


@pytest.mark.anyio
async def test_skill_declarations_do_not_persist_body_or_runtime_cache(tmp_path: Path) -> None:
    body_plaintext = "body-plaintext-should-not-be-in-resource-store"
    secret_default = "secret-default-should-not-be-in-resource-store"
    user_dir = tmp_path / "skills"
    _write_skill(
        user_dir,
        "alpha",
        "Alpha",
        body=body_plaintext,
        setup_secret_default=secret_default,
    )

    mt = await _module_table(tmp_path)
    manager = SkillManager(mt, user_skills_dir=user_dir, project_skills_dir=tmp_path / "project")
    await manager.startup()
    try:
        assert manager.activate("alpha") is not None
        store = ResourceStore.open(tmp_path)
        try:
            payload = store.read_tx(
                lambda conn: conn.execute(
                    "SELECT payload_json FROM config_sections "
                    "WHERE file = 'skills' AND section = 'global_declarations'"
                ).fetchone()[0]
            )
            export_path = tmp_path / "export.json"
            store.export("json", export_path, dry_run=False)
            exported = orjson.dumps(orjson.loads(export_path.read_bytes())).decode()
        finally:
            store.close()

        assert body_plaintext not in payload
        assert secret_default not in payload
        assert body_plaintext not in exported
        assert secret_default not in exported
    finally:
        await manager.shutdown()
        mt.config.close()
