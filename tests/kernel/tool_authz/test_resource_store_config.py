from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from kernel.agents.access.security import AuthContext
from kernel.agents.mustang.module_table import KernelModuleTable
from kernel.agents.mustang.orchestrator.types import ToolKind
from kernel.agents.mustang.tool_authz import ToolAuthorizer
from kernel.agents.mustang.tool_authz.config_section import PermissionsSection
from kernel.agents.mustang.tool_authz.types import AuthorizeContext, PermissionAllow, PermissionDeny
from kernel.agents.mustang.tools.tool import Tool
from kernel.agents.mustang.tools.types import PermissionSuggestion, ToolCallProgress, ToolCallResult
from kernel.core.config import ConfigManager
from kernel.core.config.sqlite_backend import ConfigSQLiteBackend
from kernel.core.flags import FlagManager
from kernel.core.storage import ResourceStore


class EchoTool(Tool[dict[str, Any], str]):
    name = "Echo"
    description = "test"
    kind = ToolKind.read

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        return PermissionSuggestion(risk="low", default_decision="allow", reason="test")

    def prepare_permission_matcher(self, input: dict[str, Any]):  # noqa: ANN201
        return lambda _pattern: True

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        yield ToolCallResult(data="", llm_content=[], display=None)  # type: ignore[arg-type]


async def _module_table(
    home: Path,
    *,
    project_dir: Path | None = None,
    cli_overrides: tuple[str, ...] = (),
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
    return KernelModuleTable(flags=flags, config=config, state_dir=state_dir)


def _write_permissions(home: Path, payload: dict[str, Any]) -> int:
    store = ResourceStore.open(home)
    try:
        record = ConfigSQLiteBackend(store).write(
            file="config",
            section="permissions",
            payload=payload,
            expected_revision=None,
            actor="test",
        )
        return record.revision
    finally:
        store.close()


def _auth_context() -> AuthorizeContext:
    return AuthorizeContext(
        session_id="s-1",
        agent_depth=0,
        mode="default",
        cwd=Path.cwd(),
        should_avoid_prompts=False,
        connection_auth=AuthContext(
            connection_id="test",
            credential_type="token",
            remote_addr="127.0.0.1:1",
            authenticated_at=datetime.now(timezone.utc),
        ),
    )


@pytest.mark.anyio
async def test_tool_policy_startup_from_resource_store(tmp_path: Path) -> None:
    _write_permissions(tmp_path, {"deny": ["Echo"]})
    mt = await _module_table(tmp_path)
    authorizer = ToolAuthorizer(mt)
    await authorizer.startup()
    try:
        assert authorizer.filter_denied_tools({"Echo", "Bash"}) == {"Echo"}
    finally:
        await authorizer.shutdown()
        mt.config.close()


@pytest.mark.anyio
async def test_tool_policy_revision_refresh_updates_authorizer(tmp_path: Path) -> None:
    first_revision = _write_permissions(tmp_path, {"allow": ["Echo"]})
    mt = await _module_table(tmp_path)
    authorizer = ToolAuthorizer(mt)
    await authorizer.startup()
    try:
        tool = EchoTool()
        decision = await authorizer.authorize(tool=tool, tool_input={}, ctx=_auth_context())
        assert isinstance(decision, PermissionAllow)

        store = ResourceStore.open(tmp_path)
        try:
            updated = ConfigSQLiteBackend(store).write(
                file="config",
                section="permissions",
                payload={"deny": ["Echo"]},
                expected_revision=first_revision,
                actor="test",
            )
        finally:
            store.close()

        mt.config.refresh_from_resource_store()
        assert mt.config.current_revisions()["config.global._.config.permissions"] == updated.revision
        denied = await authorizer.authorize(tool=tool, tool_input={}, ctx=_auth_context())
        assert isinstance(denied, PermissionDeny)
    finally:
        await authorizer.shutdown()
        mt.config.close()


@pytest.mark.anyio
async def test_project_local_override_still_wins_over_global_resource_store(
    tmp_path: Path,
) -> None:
    _write_permissions(tmp_path, {"allow": ["Echo"]})
    project_dir = tmp_path / "project-config"
    project_dir.mkdir()
    (project_dir / "config.local.yaml").write_text(
        yaml.safe_dump({"permissions": {"deny": ["Echo"]}}),
        encoding="utf-8",
    )

    mt = await _module_table(tmp_path, project_dir=project_dir)
    authorizer = ToolAuthorizer(mt)
    await authorizer.startup()
    try:
        assert authorizer.filter_denied_tools({"Echo"}) == {"Echo"}
    finally:
        await authorizer.shutdown()
        mt.config.close()


@pytest.mark.anyio
async def test_cli_override_still_wins_over_global_resource_store(tmp_path: Path) -> None:
    _write_permissions(tmp_path, {"allow": ["Echo"]})
    mt = await _module_table(
        tmp_path,
        cli_overrides=("config.permissions.deny=[Echo]",),
    )
    authorizer = ToolAuthorizer(mt)
    await authorizer.startup()
    try:
        section = mt.config.get_section(
            file="config",
            section="permissions",
            schema=PermissionsSection,
        )
        assert section.get().deny == ["Echo"]
        assert authorizer.filter_denied_tools({"Echo"}) == {"Echo"}
    finally:
        await authorizer.shutdown()
        mt.config.close()
