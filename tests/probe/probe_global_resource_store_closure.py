from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel

from kernel.access_router.gateway_commands import GatewayCommandService
from kernel.access_router.repository import AccessRouterRepository
from kernel.access_router.router import AccessRouter
from kernel.access_router.schemas import DeliverTurnRequest, RuntimeRegisterRequest
from kernel.agent_hub.manager.command_surface import AgentCommandService
from kernel.agent_hub.manager.manager import AgentManager
from kernel.agent_hub.manager.schemas import (
    GrantCapability,
    ResourceScope,
)
from kernel.agents.access.security.context import AuthContext
from kernel.agents.mustang.schedule import ScheduleConfig, ScheduleFlags, ScheduleManager
from kernel.agents.mustang.schedule.store import CronStore
from kernel.agents.mustang.schedule.types import CronTask, CronTaskStatus, Schedule, ScheduleKind
from kernel.core.config import ConfigManager
from kernel.core.config.sqlite_backend import ConfigSQLiteBackend
from kernel.core.flags import FlagManager
from kernel.core.flags.sqlite_backend import FlagSQLiteBackend
from kernel.core.protocol.acp.codec import AcpCodec
from kernel.core.protocol.acp.namespaces import MustangMethod
from kernel.core.protocol.acp.session_handler import AcpSessionHandler
from kernel.core.secrets import SecretManager
from kernel.core.storage import ResourceStore, tables


class ProbeToolsConfig(BaseModel):
    bash_timeout: int = 120


class _ManagementModuleTable:
    def __init__(
        self,
        *,
        home: Path,
        flags: FlagManager,
        secrets: SecretManager,
        agents: AgentCommandService,
        gateways: GatewayCommandService,
    ) -> None:
        self.state_dir = home / "state"
        self.state_dir.mkdir(exist_ok=True)
        self.flags = flags
        self.secrets = secrets
        self.agent_command_service = agents
        self.gateway_command_service = gateways


class _ScheduleFlags:
    def register(self, _section: str, schema: type[Any]) -> Any:
        return schema()

    def get_section(self, _section: str) -> ScheduleFlags:
        return ScheduleFlags()


class _ScheduleConfigSection:
    def get(self) -> ScheduleConfig:
        return ScheduleConfig()


class _ScheduleConfig:
    def get_section(self, **_: Any) -> _ScheduleConfigSection:
        return _ScheduleConfigSection()


class _ScheduleSessionManager:
    async def delete_session(self, _session_id: str) -> bool:
        return False


class _ScheduleModuleTable:
    def __init__(self, home: Path) -> None:
        self.state_dir = home / "state"
        self.state_dir.mkdir(exist_ok=True)
        self.flags = _ScheduleFlags()
        self.config = _ScheduleConfig()
        self.session = _ScheduleSessionManager()

    def get(self, cls: type[Any]) -> Any:
        from kernel.agents.mustang.sessions import SessionManager

        if cls is SessionManager:
            return self.session
        raise KeyError(cls)


def _auth(connection_id: str) -> AuthContext:
    return AuthContext(
        connection_id=connection_id,
        credential_type="token",
        remote_addr="127.0.0.1:1",
        authenticated_at=datetime.now(timezone.utc),
    )


async def _request(
    dispatcher: AcpSessionHandler,
    codec: AcpCodec,
    method: str,
    params: dict[str, Any],
    *,
    request_id: int,
) -> dict[str, Any]:
    auth = _auth(f"global-resource-closure-{request_id}")
    init = codec.decode(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientInfo": {"name": "probe", "title": "Probe"},
                },
            }
        )
    )
    async for _ in dispatcher.dispatch(init, auth):
        pass

    msg = codec.decode(
        json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    )
    frames = [json.loads(codec.encode(frame)) async for frame in dispatcher.dispatch(msg, auth)]
    return frames[-1]


async def _main() -> None:
    with tempfile.TemporaryDirectory(prefix="mustang-global-resource-closure-") as raw_home:
        home = Path(raw_home)
        config_dir = home / "config"
        config_dir.mkdir()
        workspace = home / "workspace"
        workspace.mkdir()
        state_dir = home / "agents" / "worker"
        state_dir.mkdir(parents=True)
        (state_dir / "state.json").write_text("{}", encoding="utf-8")

        config_refresh = await _probe_config(home)
        (
            flags_frozen_snapshot,
            flags_restart_applied,
            flags_pending_restart,
        ) = await _probe_flags(home)

        flags = FlagManager(path=home / "missing-flags.yaml", resource_home=home)
        await flags.initialize()
        secrets = SecretManager(db_path=home / "secrets.db")
        await secrets.startup()
        manager = AgentManager(home=home)
        manager.startup()
        repo = AccessRouterRepository.open(home)
        router = AccessRouter(auth_token="secret")
        seen_turns: list[DeliverTurnRequest] = []

        async def handler(request: DeliverTurnRequest) -> dict[str, object]:
            seen_turns.append(request)
            return {"reply": f"from-{request.agent_id}"}

        router.register_runtime(_register("worker"), handler)
        repo.declare_adapter(
            adapter_id="test",
            adapter_type="test",
            config={},
            enabled=True,
            actor="primary",
        )
        agents = AgentCommandService(manager=manager, gateway_repository=repo, router=router)
        gateways = GatewayCommandService(repo)
        dispatcher = AcpSessionHandler(
            _ManagementModuleTable(
                home=home,
                flags=flags,
                secrets=secrets,
                agents=agents,
                gateways=gateways,
            )
        )
        codec = AcpCodec()

        schedule_store = CronStore()
        await schedule_store.startup_resource(home)
        try:
            secret_checks = await _probe_secret_and_acp(dispatcher, codec, secrets, home)
            global_checks = await _probe_global_acp(dispatcher, codec, home)
            flag_acp_checks = await _probe_flags_acp(dispatcher, codec, flags, home)
            route_checks = await _probe_agent_gateway_routing(
                dispatcher,
                codec,
                repo,
                router,
                seen_turns,
                home,
                workspace,
                state_dir,
            )
            schedule_checks = await _probe_schedule(home)
            delete_checks = await _probe_agent_delete(
                dispatcher,
                codec,
                manager,
                schedule_store,
                workspace,
                state_dir,
                home,
            )
        finally:
            await schedule_store.shutdown()
            router.close()
            repo.close()
            manager.close()
            flags.close()
            secrets.close()

        checks = {
            "probe": "global_resource_store_closure",
            "config_refresh": config_refresh,
            "flags_frozen_snapshot": flags_frozen_snapshot
            and flags_restart_applied
            and flags_pending_restart,
            **secret_checks,
            **global_checks,
            **flag_acp_checks,
            **route_checks,
            **delete_checks,
            **schedule_checks,
        }
        for key, value in checks.items():
            print(f"{key}={value}")

        assert checks["config_refresh"] is True
        assert checks["flags_frozen_snapshot"] is True
        assert checks["secret_ref_stable"] is True
        assert checks["secrets_plaintext_leaked"] is False
        assert checks["global_dry_run"] is True
        assert checks["flags_acp_after_restart"] is True
        assert checks["secrets_acp_metadata"] is True
        assert checks["agents_gateways_share_access_channel_bindings"] is True
        assert checks["agent_bindings"] == 0
        assert checks["agent_send_via_access_router"] is True
        assert checks["agent_send_route_unavailable_typed"] is True
        assert checks["agent_delete_disabled_bindings"] is True
        assert checks["agent_delete_revoked_grants"] is True
        assert checks["agent_delete_disabled_schedules"] is True
        assert checks["workspace_preserved"] is True
        assert checks["schedule_startup_from_resource_store"] is True
        print("result=PASS")


async def _probe_config(home: Path) -> bool:
    store = ResourceStore.open(home)
    try:
        first = ConfigSQLiteBackend(store).write(
            file="config",
            section="tools",
            payload={"bash_timeout": 60},
            expected_revision=None,
            actor="primary",
        )
    finally:
        store.close()

    manager = ConfigManager(global_dir=home / "yaml", resource_home=home)
    await manager.startup()
    try:
        section = manager.get_section(file="config", section="tools", schema=ProbeToolsConfig)
        initial = section.get().bash_timeout == 60
        store = ResourceStore.open(home)
        try:
            ConfigSQLiteBackend(store).write(
                file="config",
                section="tools",
                payload={"bash_timeout": 90},
                expected_revision=first.revision,
                actor="primary",
            )
        finally:
            store.close()
        manager.refresh_from_resource_store()
        return (
            initial
            and section.get().bash_timeout == 90
            and manager.current_revisions().get("config.global._.config.tools") == 2
        )
    finally:
        manager.close()


async def _probe_flags(home: Path) -> tuple[bool, bool, bool]:
    store = ResourceStore.open(home)
    try:
        first = FlagSQLiteBackend(store).write(
            section="kernel",
            payload={"memory": True},
            expected_revision=None,
            actor="primary",
        )
    finally:
        store.close()

    manager = FlagManager(path=home / "missing-flags.yaml", resource_home=home)
    await manager.initialize()
    try:
        startup_value = manager.get_section("kernel").memory
        store = ResourceStore.open(home)
        try:
            FlagSQLiteBackend(store).write(
                section="kernel",
                payload={"memory": False},
                expected_revision=first.revision,
                actor="primary",
            )
        finally:
            store.close()
        frozen = startup_value is True and manager.get_section("kernel").memory is True
        pending = manager.management_read("kernel")["pending_restart"] is True
    finally:
        manager.close()

    restarted = FlagManager(path=home / "missing-flags.yaml", resource_home=home)
    await restarted.initialize()
    try:
        restart_applied = restarted.get_section("kernel").memory is False
    finally:
        restarted.close()
    return frozen, restart_applied, pending


async def _probe_secret_and_acp(
    dispatcher: AcpSessionHandler,
    codec: AcpCodec,
    secrets: SecretManager,
    home: Path,
) -> dict[str, bool]:
    ref = secrets.create("probe-api-key", b"sk-probe-hidden", actor="primary")
    renamed = secrets.rename(
        ref.secret_id,
        "renamed-probe-api-key",
        expected_revision=ref.revision,
        actor="primary",
    )
    resolved = secrets.resolve_id(ref.ref) == b"sk-probe-hidden"
    listed = await _request(dispatcher, codec, MustangMethod.SECRETS_LIST, {}, request_id=100)
    audit = await _request(dispatcher, codec, MustangMethod.SECRETS_AUDIT, {}, request_id=101)
    delete_rejected = await _request(
        dispatcher,
        codec,
        MustangMethod.SECRETS_DELETE,
        {"secretId": ref.secret_id, "expectedRevision": renamed.revision, "confirm": False},
        request_id=102,
    )
    deleted = await _request(
        dispatcher,
        codec,
        MustangMethod.SECRETS_DELETE,
        {"secretId": ref.secret_id, "expectedRevision": renamed.revision, "confirm": True},
        request_id=103,
    )
    exported = _dump_exportable_resource_text(home)
    payload_text = json.dumps([listed, audit, delete_rejected, deleted], sort_keys=True)
    plaintext_leaked = "sk-probe-hidden" in exported or "sk-probe-hidden" in payload_text
    return {
        "secret_ref_stable": renamed.ref == ref.ref and resolved,
        "secrets_plaintext_leaked": plaintext_leaked,
        "secrets_acp_metadata": "error" in delete_rejected and deleted["result"]["deleted"] is True,
    }


async def _probe_global_acp(
    dispatcher: AcpSessionHandler,
    codec: AcpCodec,
    home: Path,
) -> dict[str, bool]:
    store = ResourceStore.open(home)
    try:
        store.cas_put_resource("probe.resource", '{"value":1}', actor="primary")
    finally:
        store.close()
    backup = await _request(dispatcher, codec, MustangMethod.GLOBAL_BACKUP, {}, request_id=200)
    backups = await _request(dispatcher, codec, MustangMethod.GLOBAL_BACKUPS, {}, request_id=201)
    export_path = home / "global-export.json"
    export = await _request(
        dispatcher,
        codec,
        MustangMethod.GLOBAL_EXPORT,
        {"outputPath": str(export_path), "dryRun": False},
        request_id=202,
    )
    dry_run = await _request(
        dispatcher,
        codec,
        MustangMethod.GLOBAL_IMPORT,
        {"inputPath": str(export_path), "dryRun": True},
        request_id=203,
    )
    apply = await _request(
        dispatcher,
        codec,
        MustangMethod.GLOBAL_IMPORT,
        {"inputPath": str(export_path), "dryRun": False},
        request_id=204,
    )
    return {
        "global_dry_run": Path(backup["result"]["path"]).exists()
        and bool(backups["result"]["backups"])
        and export["result"]["resourceCount"] >= 1
        and dry_run["result"]["dryRun"] is True
        and apply["result"]["unavailable"] is True,
    }


async def _probe_flags_acp(
    dispatcher: AcpSessionHandler,
    codec: AcpCodec,
    flags: FlagManager,
    home: Path,
) -> dict[str, bool]:
    before = flags.get_section("kernel").memory
    listed = await _request(dispatcher, codec, MustangMethod.FLAGS_LIST, {}, request_id=300)
    read = await _request(
        dispatcher,
        codec,
        MustangMethod.FLAGS_READ,
        {"section": "kernel"},
        request_id=301,
    )
    set_result = await _request(
        dispatcher,
        codec,
        MustangMethod.FLAGS_SET,
        {
            "section": "kernel",
            "key": "memory",
            "value": not before,
            "expectedRevision": read["result"]["revision"],
        },
        request_id=302,
    )
    reset_result = await _request(
        dispatcher,
        codec,
        MustangMethod.FLAGS_RESET,
        {
            "section": "kernel",
            "key": "memory",
            "expectedRevision": set_result["result"]["revision"],
        },
        request_id=303,
    )
    store = ResourceStore.open(home)
    try:
        revisions = store.current_revisions("flags.")
    finally:
        store.close()
    return {
        "flags_acp_after_restart": listed["result"]["sections"]
        and read["result"]["section"] == "kernel"
        and set_result["result"]["applies"] == "after_restart"
        and set_result["result"]["pendingRestart"] is True
        and reset_result["result"]["pendingRestart"] is True
        and flags.get_section("kernel").memory is before
        and revisions.get("flags.kernel") == reset_result["result"]["revision"],
    }


async def _probe_agent_gateway_routing(
    dispatcher: AcpSessionHandler,
    codec: AcpCodec,
    repo: AccessRouterRepository,
    router: AccessRouter,
    seen_turns: list[DeliverTurnRequest],
    home: Path,
    workspace: Path,
    state_dir: Path,
) -> dict[str, bool | int]:
    await _request(
        dispatcher,
        codec,
        MustangMethod.AGENTS_ADD,
        {"agentId": "worker", "workspace": str(workspace), "stateDir": str(state_dir)},
        request_id=400,
    )
    agent_bind = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENTS_BIND,
        {"agentId": "worker", "bind": "test:chan-1", "sessionId": "session-a"},
        request_id=401,
    )
    gateway_view = await _request(
        dispatcher,
        codec,
        MustangMethod.GATEWAYS_BINDINGS,
        {"gatewayId": "test"},
        request_id=402,
    )
    gateway_bind = await _request(
        dispatcher,
        codec,
        MustangMethod.GATEWAYS_BIND,
        {"gatewayId": "test", "channelKey": "chan-2", "agentId": "worker"},
        request_id=403,
    )
    agent_view = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENTS_BINDINGS,
        {"agentId": "worker"},
        request_id=404,
    )
    send = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENT_SEND,
        {"agentId": "worker", "message": "hello worker"},
        request_id=405,
    )
    unavailable = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENT_SEND,
        {"agentId": "ghost", "message": "hello ghost"},
        request_id=406,
    )
    access_bindings = repo.list_channel_bindings()
    agent_bindings = _agent_bindings_count(home)
    return {
        "agents_gateways_share_access_channel_bindings": agent_bind["result"]["binding"][
            "bindingId"
        ]
        == gateway_view["result"]["bindings"][0]["bindingId"]
        and gateway_bind["result"]["binding"]["bindingId"]
        in {row["bindingId"] for row in agent_view["result"]["bindings"]}
        and len(access_bindings) == 2,
        "agent_bindings": agent_bindings,
        "agent_send_via_access_router": send["result"]["delivered"] is True
        and bool(seen_turns)
        and seen_turns[0].prompt == "hello worker"
        and router.agent_hub_forward_count == 0,
        "agent_send_route_unavailable_typed": unavailable["result"]["errorCode"]
        == "route_unavailable",
    }


async def _probe_agent_delete(
    dispatcher: AcpSessionHandler,
    codec: AcpCodec,
    manager: AgentManager,
    schedule_store: CronStore,
    workspace: Path,
    state_dir: Path,
    home: Path,
) -> dict[str, bool | int]:
    task = CronTask(
        id="worker-owned-task",
        owner_agent_id="worker",
        schedule=Schedule(kind=ScheduleKind.every, interval_seconds=120),
        prompt="run worker job",
        created_at=1,
        next_fire_at=2,
    )
    await schedule_store.add(task)
    grant = manager.grant(
        "worker",
        GrantCapability.GLOBAL_RESOURCE_WRITE,
        ResourceScope.GLOBAL,
        granted_by_agent_id="primary",
    )
    worker = manager.get("worker")
    assert worker is not None
    deleted = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENTS_DELETE,
        {"agentId": "worker", "expectedRevision": worker.revision, "confirm": True},
        request_id=500,
    )
    deleted_task = await schedule_store.get("worker-owned-task")
    store = ResourceStore.open(home)
    try:
        row = store.read_tx(
            lambda conn: conn.execute(
                sa.select(tables.agent_definitions).where(
                    tables.agent_definitions.c.agent_id == "worker"
                )
            ).fetchone()
        )
        enabled_count = store.read_tx(
            lambda conn: conn.execute(
                sa.select(sa.func.count())
                .select_from(tables.access_channel_bindings)
                .where(
                    tables.access_channel_bindings.c.target_agent_id == "worker",
                    tables.access_channel_bindings.c.enabled == 1,
                )
            ).fetchone()[0]
        )
        revoked = store.read_tx(
            lambda conn: conn.execute(
                sa.select(tables.management_grants.c.revoked_at).where(
                    tables.management_grants.c.grant_id == grant.grant_id
                )
            ).fetchone()[0]
        )
    finally:
        store.close()
    return {
        "agent_delete_status_deleted": row["status"] == "deleted"
        and deleted["result"]["deleted"] is True,
        "agent_delete_disabled_bindings": enabled_count == 0,
        "agent_delete_revoked_grants": revoked is not None,
        "agent_delete_disabled_schedules": deleted_task is not None
        and deleted_task.status == CronTaskStatus.paused
        and deleted_task.next_fire_at is None,
        "workspace_preserved": workspace.exists(),
        "state_dir_cleanup_done": not state_dir.exists()
        or row["state_dir_deletion_status"] in {"pending", "deleted"},
        "agent_bindings_after_delete": _agent_bindings_count(home),
    }


async def _probe_schedule(home: Path) -> dict[str, bool]:
    config_dir = home / "config"
    legacy_file = config_dir / "schedules.yaml"
    legacy_file.write_text(
        """
tasks:
  - id: legacy-task
    owner_agent_id: primary
    schedule:
      kind: every
      interval_seconds: 60
    prompt: legacy prompt
    next_fire_at: 2000000000
""",
        encoding="utf-8",
    )
    store = CronStore()
    await store.startup_resource(home)
    try:
        legacy = await store.get("legacy-task")
    finally:
        await store.shutdown()

    subsystem = ScheduleManager(_ScheduleModuleTable(home))  # type: ignore[arg-type]
    await subsystem.startup()
    try:
        startup_task = await subsystem.get_task("legacy-task")
    finally:
        await subsystem.shutdown()

    legacy_file.write_text(
        """
tasks:
  - id: drift-task
    schedule:
      kind: every
      interval_seconds: 30
    prompt: drift
""",
        encoding="utf-8",
    )
    store = CronStore()
    await store.startup_resource(home)
    try:
        drift_ignored = await store.get("drift-task") is None and bool(store.legacy_import_warnings)
        task = CronTask(
            id="schedule-revision-task",
            owner_agent_id="primary",
            schedule=Schedule(kind=ScheduleKind.every, interval_seconds=30),
            prompt="revision task",
            created_at=1,
            next_fire_at=2,
        )
        await store.add(task)
        revision_after_add = store.current_revision("schedule-revision-task")
        await store.update_status(
            "schedule-revision-task",
            CronTaskStatus.paused,
            next_fire_at=None,
        )
        revision_after_update = store.current_revision("schedule-revision-task")
    finally:
        await store.shutdown()
    return {
        "schedule_startup_from_resource_store": legacy is not None
        and startup_task is not None
        and drift_ignored
        and revision_after_add == 1
        and revision_after_update == 2,
    }


def _register(agent_id: str) -> RuntimeRegisterRequest:
    return RuntimeRegisterRequest(
        process_id=f"runtime-{agent_id}",
        pid=123,
        agent_id=agent_id,
        protocol_version=1,
        capabilities=("session",),
        auth_token="secret",
    )


def _agent_bindings_count(home: Path) -> int:
    store = ResourceStore.open(home)
    try:
        return int(
            store.read_tx(
                lambda conn: conn.execute(
                    sa.select(sa.func.count()).select_from(tables.agent_bindings)
                ).fetchone()[0]
            )
        )
    finally:
        store.close()


def _dump_exportable_resource_text(home: Path) -> str:
    store = ResourceStore.open(home)
    try:
        rows = store.read_tx(
            lambda conn: conn.execute(
                sa.select(tables.global_resources.c.payload_json).order_by(
                    tables.global_resources.c.resource_key
                )
            ).fetchall()
        )
    finally:
        store.close()
    return json.dumps([row["payload_json"] for row in rows], sort_keys=True)


if __name__ == "__main__":
    asyncio.run(_main())
