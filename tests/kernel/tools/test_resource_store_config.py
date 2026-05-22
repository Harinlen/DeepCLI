from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson
import pytest
import yaml

from kernel.agents.mustang.module_table import KernelModuleTable
from kernel.agents.mustang.tools import ToolManager
from kernel.agents.mustang.tools.web.management import get_definition
from kernel.core.config import ConfigManager
from kernel.core.config.sqlite_backend import ConfigSQLiteBackend
from kernel.core.flags import FlagManager
from kernel.core.secrets import SecretManager
from kernel.core.storage import ResourceStore


async def _module_table(
    home: Path,
    *,
    project_dir: Path | None = None,
    cli_overrides: tuple[str, ...] = (),
    secrets: SecretManager | None = None,
) -> KernelModuleTable:
    flags = FlagManager(resource_home=home)
    await flags.initialize()
    config = ConfigManager(
        resource_home=home,
        project_dir=project_dir,
        cli_overrides=cli_overrides,
    )
    await config.startup()
    state_dir = home / "state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    return KernelModuleTable(flags=flags, config=config, state_dir=state_dir, secrets=secrets)


def _write_web_fetch(home: Path, payload: dict[str, Any]) -> int:
    store = ResourceStore.open(home)
    try:
        record = ConfigSQLiteBackend(store).write(
            file="config",
            section="web_fetch",
            payload=payload,
            expected_revision=None,
            actor="test",
        )
        return record.revision
    finally:
        store.close()


@pytest.mark.anyio
async def test_web_fetch_config_startup_from_resource_store(tmp_path: Path) -> None:
    _write_web_fetch(tmp_path, {"backend": "httpx"})
    mt = await _module_table(tmp_path)
    manager = ToolManager(mt)
    await manager.startup()
    try:
        assert manager.web_fetch_config_model().backend == "httpx"
        assert mt.config.current_revisions()["config.global._.config.web_fetch"] == 1
    finally:
        await manager.shutdown()
        mt.config.close()


@pytest.mark.anyio
async def test_web_fetch_config_revision_refresh_updates_section(tmp_path: Path) -> None:
    first_revision = _write_web_fetch(tmp_path, {"backend": "httpx"})
    mt = await _module_table(tmp_path)
    manager = ToolManager(mt)
    await manager.startup()
    try:
        store = ResourceStore.open(tmp_path)
        try:
            updated = ConfigSQLiteBackend(store).write(
                file="config",
                section="web_fetch",
                payload={"backend": "parallel"},
                expected_revision=first_revision,
                actor="test",
            )
        finally:
            store.close()

        mt.config.refresh_from_resource_store()
        assert manager.web_fetch_config_model().backend == "parallel"
        assert mt.config.current_revisions()["config.global._.config.web_fetch"] == updated.revision
    finally:
        await manager.shutdown()
        mt.config.close()


@pytest.mark.anyio
async def test_web_fetch_project_local_and_cli_override_win(
    tmp_path: Path,
) -> None:
    _write_web_fetch(tmp_path, {"backend": "httpx"})
    project_dir = tmp_path / "project-config"
    project_dir.mkdir()
    (project_dir / "config.local.yaml").write_text(
        yaml.safe_dump({"web_fetch": {"backend": "parallel"}}),
        encoding="utf-8",
    )

    project_mt = await _module_table(tmp_path, project_dir=project_dir)
    project_manager = ToolManager(project_mt)
    await project_manager.startup()
    try:
        assert project_manager.web_fetch_config_model().backend == "parallel"
    finally:
        await project_manager.shutdown()
        project_mt.config.close()

    cli_mt = await _module_table(
        tmp_path,
        project_dir=tmp_path / "empty-project",
        cli_overrides=("config.web_fetch.backend=tavily",),
    )
    cli_manager = ToolManager(cli_mt)
    await cli_manager.startup()
    try:
        assert cli_manager.web_fetch_config_model().backend == "tavily"
    finally:
        await cli_manager.shutdown()
        cli_mt.config.close()


@pytest.mark.anyio
async def test_web_fetch_api_key_ref_uses_secret_uuid_and_export_has_no_plaintext(
    tmp_path: Path,
) -> None:
    _write_web_fetch(tmp_path, {"backend": "auto"})
    secrets = SecretManager(db_path=tmp_path / "secrets.db")
    await secrets.startup()
    mt = await _module_table(tmp_path, secrets=secrets)
    manager = ToolManager(mt)
    await manager.startup()
    try:
        definition = get_definition("tavily")
        assert definition is not None
        await manager._store_web_fetch_api_key(definition, "tvly-secret-plaintext")
        config = manager.web_fetch_config_model()
        secret_ref = config.backends["tavily"]["api_key_ref"]
        assert secret_ref.startswith("secret:")
        assert secrets.get(secret_ref) == "tvly-secret-plaintext"

        store = ResourceStore.open(tmp_path)
        try:
            payload = store.read_tx(
                lambda conn: conn.execute(
                    "SELECT payload_json FROM config_sections WHERE file = 'config' AND section = 'web_fetch'"
                ).fetchone()[0]
            )
            export_path = tmp_path / "export.json"
            store.export("json", export_path, dry_run=False)
            exported = orjson.dumps(orjson.loads(export_path.read_bytes())).decode()
        finally:
            store.close()

        assert secret_ref in payload
        assert "tvly-secret-plaintext" not in payload
        assert "tvly-secret-plaintext" not in exported
    finally:
        await manager.shutdown()
        mt.config.close()
        secrets.close()
