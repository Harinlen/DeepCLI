from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import orjson
import yaml

from kernel.agents.access.security import AuthContext
from kernel.agents.mustang.module_table import KernelModuleTable
from kernel.agents.mustang.orchestrator.types import ToolKind
from kernel.agents.mustang.tool_authz import ToolAuthorizer
from kernel.agents.mustang.tool_authz.types import AuthorizeContext, PermissionAllow, PermissionDeny
from kernel.agents.mustang.tools import ToolManager
from kernel.agents.mustang.tools.tool import Tool
from kernel.agents.mustang.tools.types import PermissionSuggestion, ToolCallProgress, ToolCallResult
from kernel.agents.mustang.tools.web.management import get_definition
from kernel.core.config import ConfigManager
from kernel.core.config.sqlite_backend import ConfigSQLiteBackend
from kernel.core.flags import FlagManager
from kernel.core.secrets import SecretManager
from kernel.core.storage import ResourceStore


class EchoTool(Tool[dict[str, Any], str]):
    name = "Echo"
    description = "test"
    kind = ToolKind.read

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        return PermissionSuggestion(risk="low", default_decision="allow", reason="probe")

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        yield ToolCallResult(data="", llm_content=[], display=None)  # type: ignore[arg-type]


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


def _write_config(home: Path, section: str, payload: dict[str, Any], revision: int | None) -> int:
    store = ResourceStore.open(home)
    try:
        record = ConfigSQLiteBackend(store).write(
            file="config",
            section=section,
            payload=payload,
            expected_revision=revision,
            actor="probe",
        )
        return record.revision
    finally:
        store.close()


def _ctx() -> AuthorizeContext:
    return AuthorizeContext(
        session_id="probe-session",
        agent_depth=0,
        mode="default",
        cwd=Path.cwd(),
        should_avoid_prompts=False,
        connection_auth=AuthContext(
            connection_id="probe",
            credential_type="token",
            remote_addr="127.0.0.1:1",
            authenticated_at=datetime.now(timezone.utc),
        ),
    )


async def _main() -> None:
    with tempfile.TemporaryDirectory(prefix="mustang-tool-policy-resource-probe-") as raw_home:
        home = Path(raw_home)
        permissions_rev = _write_config(home, "permissions", {"allow": ["Echo"]}, None)
        web_fetch_rev = _write_config(home, "web_fetch", {"backend": "httpx"}, None)

        secrets = SecretManager(db_path=home / "secrets.db")
        await secrets.startup()
        mt = await _module_table(home, secrets=secrets)
        authorizer = ToolAuthorizer(mt)
        tools = ToolManager(mt)
        await authorizer.startup()
        await tools.startup()
        try:
            allowed = await authorizer.authorize(tool=EchoTool(), tool_input={}, ctx=_ctx())
            policy_startup_from_resource_store = isinstance(allowed, PermissionAllow)
            web_fetch_startup_from_resource_store = tools.web_fetch_config_model().backend == "httpx"

            permissions_rev = _write_config(
                home,
                "permissions",
                {"deny": ["Echo"]},
                permissions_rev,
            )
            web_fetch_rev = _write_config(
                home,
                "web_fetch",
                {"backend": "parallel"},
                web_fetch_rev,
            )
            mt.config.refresh_from_resource_store()
            denied = await authorizer.authorize(tool=EchoTool(), tool_input={}, ctx=_ctx())
            policy_refresh_updates_before_call = isinstance(denied, PermissionDeny)
            web_fetch_refresh_updates_section = tools.web_fetch_config_model().backend == "parallel"
            definition = get_definition("tavily")
            if definition is None:
                raise AssertionError("missing tavily WebFetch definition")
            await tools._store_web_fetch_api_key(definition, "tvly-probe-plaintext")
            secret_ref = tools.web_fetch_config_model().backends["tavily"]["api_key_ref"]
            revisions = mt.config.current_revisions()
        finally:
            await tools.shutdown()
            await authorizer.shutdown()
            mt.config.close()
            secrets.close()

        project_dir = home / "project-config"
        project_dir.mkdir()
        (project_dir / "config.local.yaml").write_text(
            yaml.safe_dump(
                {
                    "permissions": {"allow": ["Echo"], "deny": []},
                    "web_fetch": {"backend": "tavily"},
                }
            ),
            encoding="utf-8",
        )
        overlay_mt = await _module_table(
            home,
            project_dir=project_dir,
            cli_overrides=("config.web_fetch.backend=exa",),
        )
        overlay_authorizer = ToolAuthorizer(overlay_mt)
        overlay_tools = ToolManager(overlay_mt)
        await overlay_authorizer.startup()
        await overlay_tools.startup()
        try:
            overlay_decision = await overlay_authorizer.authorize(
                tool=EchoTool(), tool_input={}, ctx=_ctx()
            )
            project_local_override_wins = isinstance(overlay_decision, PermissionAllow)
            cli_override_wins = overlay_tools.web_fetch_config_model().backend == "exa"
        finally:
            await overlay_tools.shutdown()
            await overlay_authorizer.shutdown()
            overlay_mt.config.close()

        store = ResourceStore.open(home)
        try:
            web_payload = store.read_tx(
                lambda conn: conn.execute(
                    "SELECT payload_json FROM config_sections WHERE file = 'config' AND section = 'web_fetch'"
                ).fetchone()[0]
            )
            export_path = home / "global-export.json"
            store.export("json", export_path, dry_run=False)
            exported = orjson.dumps(orjson.loads(export_path.read_bytes())).decode()
        finally:
            store.close()

        plaintext_leaked = "tvly-probe-plaintext" in web_payload or "tvly-probe-plaintext" in exported
        checks = {
            "tool_policy_startup_from_resource_store": policy_startup_from_resource_store,
            "web_fetch_startup_from_resource_store": web_fetch_startup_from_resource_store,
            "permissions_revision": revisions.get("config.global._.config.permissions"),
            "web_fetch_revision": revisions.get("config.global._.config.web_fetch"),
            "policy_refresh_updates_before_call": policy_refresh_updates_before_call,
            "web_fetch_refresh_updates_section": web_fetch_refresh_updates_section,
            "project_local_override_wins": project_local_override_wins,
            "cli_override_wins": cli_override_wins,
            "web_fetch_secret_ref_stable": isinstance(secret_ref, str) and secret_ref.startswith("secret:"),
            "tool_config_plaintext_leaked": plaintext_leaked,
        }

        print("probe=tool_policy_resource_store")
        for key, value in checks.items():
            print(f"{key}={value}")

        assert checks["tool_policy_startup_from_resource_store"] is True
        assert checks["web_fetch_startup_from_resource_store"] is True
        assert checks["permissions_revision"] == permissions_rev
        assert checks["web_fetch_revision"] == web_fetch_rev + 1
        assert checks["policy_refresh_updates_before_call"] is True
        assert checks["web_fetch_refresh_updates_section"] is True
        assert checks["project_local_override_wins"] is True
        assert checks["cli_override_wins"] is True
        assert checks["web_fetch_secret_ref_stable"] is True
        assert checks["tool_config_plaintext_leaked"] is False
        print("result=PASS")


if __name__ == "__main__":
    asyncio.run(_main())
