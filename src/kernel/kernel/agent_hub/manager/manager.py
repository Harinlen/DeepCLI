"""Agent Hub Manager skeleton.

The Manager owns runtime state and materializes read-only plans from
ConfigManager-owned AgentDefinitions.  It does not deliver messages and does
not run agent loops.
"""

from __future__ import annotations

import orjson
import sqlalchemy as sa
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from kernel.agent_hub.contracts import (
    AgentDefinition,
    AgentRuntimeRecord,
    AgentStatus,
    BindingPlan,
    BindingPlanEntry,
    CallerIdentity,
    CallerIdentityKind,
    ManagementCapability,
    PlatformBinding,
    RoutingSnapshot,
    RoutingSnapshotEntry,
    StatusSnapshot,
)
from kernel.agent_hub.manager.runtime_process import build_runtime_launch, spawn_runtime
from kernel.agent_hub.manager.schemas import (
    AgentDefinitionRecord,
    AgentDirectorySnapshot,
    AgentHealth,
    AgentRuntimeSpec,
    AgentSummary,
    CreateAgentSpec,
    DeleteAgentResult,
    GrantCapability,
    ManagementGrant,
    ResourceScope,
    RuntimeStatus,
)
from kernel.core.storage import tables
from kernel.core.storage.resource_store import ResourceStore


class AgentManager:
    """ResourceStore-backed durable AgentManager.

    The Manager owns durable definitions, runtime process handles and grant
    state.  Message delivery remains outside this class.
    """

    def __init__(
        self,
        *,
        home: Path,
        route_status_reader: Any | None = None,
    ) -> None:
        self.home = home
        self._store: ResourceStore | None = None
        self._processes: dict[str, Any] = {}
        self._route_status_reader = route_status_reader

    def startup(self) -> None:
        """Open ResourceStore and bootstrap the primary Agent definition."""
        self.home.mkdir(parents=True, exist_ok=True)
        self._store = ResourceStore.open(self.home)
        if self.get("primary") is None:
            self.create(
                CreateAgentSpec(
                    agent_id="primary",
                    name="Primary",
                    workspace=self.home.parent,
                    state_dir=self.home / "agents" / "primary",
                    runtime=AgentRuntimeSpec(autostart=True),
                ),
                actor_agent_id="system",
            )

    def close(self) -> None:
        """Stop owned runtime processes and close the store."""
        for process in list(self._processes.values()):
            if process.poll() is None:
                process.terminate()
        for process in list(self._processes.values()):
            try:
                process.wait(timeout=5)
            except Exception:
                process.kill()
                process.wait()
        self._processes.clear()
        if self._store is not None:
            self._store.close()
            self._store = None

    def list(self) -> tuple[AgentSummary, ...]:
        """Return active Agent summaries."""
        rows = self._require_store().read_tx(
            lambda conn: conn.execute(
                sa.select(
                    tables.agent_definitions.c.agent_id,
                    tables.agent_definitions.c.name,
                    tables.agent_definitions.c.status,
                    tables.agent_definitions.c.revision,
                )
                .where(tables.agent_definitions.c.deleted_at.is_(None))
                .order_by(tables.agent_definitions.c.agent_id)
            ).fetchall()
        )
        return tuple(
            AgentSummary(
                agent_id=str(row["agent_id"]),
                name=str(row["name"]),
                status=str(row["status"]),
                revision=int(row["revision"]),
            )
            for row in rows
        )

    def get(self, agent_id: str) -> AgentDefinitionRecord | None:
        """Return one Agent definition, including deleted rows."""
        row = self._require_store().read_tx(
            lambda conn: conn.execute(
                sa.select(tables.agent_definitions).where(
                    tables.agent_definitions.c.agent_id == agent_id
                )
            ).fetchone()
        )
        return _definition_from_row(row) if row is not None else None

    def create(self, spec: CreateAgentSpec, *, actor_agent_id: str) -> AgentDefinitionRecord:
        """Create a durable Agent definition and bump directory revision."""
        now = _now_iso()
        payload = {
            "agent_id": spec.agent_id,
            "name": spec.name,
            "identity_json": _json(spec.identity),
            "workspace": str(spec.workspace),
            "state_dir": str(spec.state_dir),
            "runtime_json": _json(spec.runtime.model_dump()),
            "status": "active",
            "deleted_at": None,
            "state_dir_deletion_status": None,
            "revision": 1,
            "updated_at": now,
            "updated_by_agent_id": actor_agent_id,
        }

        def _write(conn: Any) -> AgentDefinitionRecord:
            existing = conn.execute(
                sa.select(tables.agent_definitions.c.agent_id).where(
                    tables.agent_definitions.c.agent_id == spec.agent_id
                )
            ).fetchone()
            if existing is not None:
                raise ValueError(f"AgentDefinition already exists: {spec.agent_id}")
            conn.execute(tables.agent_definitions.insert().values(**payload))
            _bump_directory_revision(conn)
            row = conn.execute(
                sa.select(tables.agent_definitions).where(
                    tables.agent_definitions.c.agent_id == spec.agent_id
                )
            ).fetchone()
            return _definition_from_row(row)

        return self._require_store().write_tx(_write)

    def update(
        self,
        agent_id: str,
        *,
        expected_revision: int,
        actor_agent_id: str,
        name: str | None = None,
    ) -> AgentDefinitionRecord:
        """Update a definition with CAS revision semantics."""
        return self._update_definition(
            agent_id,
            expected_revision=expected_revision,
            actor_agent_id=actor_agent_id,
            values={"name": name} if name is not None else {},
        )

    def set_identity(
        self,
        agent_id: str,
        *,
        expected_revision: int,
        actor_agent_id: str,
        name: str | None = None,
        avatar: str | None = None,
        theme: str | None = None,
        identity_patch: dict[str, object] | None = None,
    ) -> AgentDefinitionRecord:
        """Update user-facing identity fields."""
        current = self._require_active(agent_id)
        identity = dict(current.identity)
        if identity_patch:
            identity.update(identity_patch)
        if avatar is not None:
            identity["avatar"] = avatar
        if theme is not None:
            identity["theme"] = theme
        values: dict[str, object] = {"identity_json": _json(identity)}
        if name is not None:
            values["name"] = name
        return self._update_definition(
            agent_id,
            expected_revision=expected_revision,
            actor_agent_id=actor_agent_id,
            values=values,
        )

    def delete(
        self,
        agent_id: str,
        *,
        expected_revision: int,
        actor_agent_id: str,
        confirm: bool,
    ) -> DeleteAgentResult:
        """Soft-delete a durable Agent definition."""
        if not confirm:
            raise PermissionError("delete requires --confirm")
        self._require_active(agent_id)

        def _write(conn: Any) -> DeleteAgentResult:
            current = conn.execute(
                sa.select(tables.agent_definitions.c.revision).where(
                    tables.agent_definitions.c.agent_id == agent_id
                )
            ).fetchone()
            if current is None or int(current["revision"]) != expected_revision:
                raise ValueError("agent revision conflict")
            conn.execute(
                tables.agent_definitions.update()
                .where(tables.agent_definitions.c.agent_id == agent_id)
                .values(
                    status="deleted",
                    deleted_at=_now_iso(),
                    revision=expected_revision + 1,
                    updated_at=_now_iso(),
                    updated_by_agent_id=actor_agent_id,
                )
            )
            _bump_directory_revision(conn)
            return DeleteAgentResult(agent_id=agent_id, deleted=True)

        self.stop(agent_id, actor_agent_id=actor_agent_id, missing_ok=True)
        return self._require_store().write_tx(_write)

    def routing_snapshot(self) -> AgentDirectorySnapshot:
        """Return current routing directory revision and active agents."""
        revision = self._directory_revision()
        return AgentDirectorySnapshot(revision=revision, agents=self.list())

    def start(
        self,
        agent_id: str,
        *,
        actor_agent_id: str,
        router_endpoint: str,
        router_token: str,
    ) -> RuntimeStatus:
        """Spawn one Agent Runtime from its durable definition."""
        definition = self._require_active(agent_id)
        process = self._processes.get(agent_id)
        if process is None or process.poll() is not None:
            launch = build_runtime_launch(
                definition,
                router_endpoint=router_endpoint,
                router_token=router_token,
            )
            process = spawn_runtime(launch)
            self._processes[agent_id] = process
        route_status = self._route_status(agent_id)
        self._write_runtime_status(
            agent_id,
            desired_state="running",
            observed_state="running",
            pid=process.pid,
            route_status=route_status,
        )
        process_running = process.poll() is None
        return RuntimeStatus(
            agent_id=agent_id,
            desired_state="running",
            observed_state="running",
            route_status=route_status,
            pid=process.pid,
            process_running=process_running,
            healthy=process_running and route_status == "registered",
        )

    def stop(
        self,
        agent_id: str,
        *,
        actor_agent_id: str,
        missing_ok: bool = False,
    ) -> RuntimeStatus:
        """Stop one Agent Runtime process owned by this Manager."""
        if self.get(agent_id) is None:
            if missing_ok:
                return RuntimeStatus(
                    agent_id=agent_id,
                    desired_state="stopped",
                    observed_state="missing",
                )
            raise KeyError(f"Unknown agent: {agent_id}")
        process = self._processes.pop(agent_id, None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except Exception:
                process.kill()
                process.wait()
        self._write_runtime_status(
            agent_id,
            desired_state="stopped",
            observed_state="stopped",
            pid=None,
            route_status=self._route_status(agent_id),
        )
        return RuntimeStatus(
            agent_id=agent_id,
            desired_state="stopped",
            observed_state="stopped",
            route_status=self._route_status(agent_id),
            process_running=False,
            healthy=False,
        )

    def health(self, agent_id: str) -> AgentHealth:
        """Combine process state and Access Router route status."""
        definition = self.get(agent_id)
        if definition is None or definition.deleted_at is not None:
            raise KeyError(f"Unknown agent: {agent_id}")
        process = self._processes.get(agent_id)
        process_running = process is not None and process.poll() is None
        route_status = self._route_status(agent_id)
        if not process_running:
            return AgentHealth(agent_id=agent_id, healthy=False, reason="process_not_running")
        if route_status != "registered":
            return AgentHealth(agent_id=agent_id, healthy=False, reason="route_not_registered")
        return AgentHealth(agent_id=agent_id, healthy=True, reason="ok")

    def set_route_status(self, agent_id: str, route_status: str) -> None:
        """Record route status observed from Access Router."""
        process = self._processes.get(agent_id)
        self._write_runtime_status(
            agent_id,
            desired_state="running",
            observed_state="running",
            pid=process.pid if process is not None else None,
            route_status=route_status,
        )

    def list_grants(self, agent_id: str | None = None) -> tuple[ManagementGrant, ...]:
        """List non-revoked management grants."""
        def _read(conn: Any) -> list[Any]:
            stmt = sa.select(tables.management_grants).where(
                tables.management_grants.c.revoked_at.is_(None)
            )
            if agent_id is not None:
                stmt = stmt.where(tables.management_grants.c.subject_agent_id == agent_id)
            return conn.execute(stmt.order_by(tables.management_grants.c.granted_at)).fetchall()

        return tuple(_grant_from_row(row) for row in self._require_store().read_tx(_read))

    def grant(
        self,
        agent_id: str,
        capability: GrantCapability,
        resource_scope: ResourceScope,
        *,
        granted_by_agent_id: str,
        resource_id: str | None = None,
        workspace: str | None = None,
        expires_at: str | None = None,
    ) -> ManagementGrant:
        """Grant a management capability to an Agent."""
        if granted_by_agent_id != "primary":
            raise PermissionError("management grant requires primary actor")
        now = _now_iso()
        grant_id = str(uuid4())

        def _write(conn: Any) -> ManagementGrant:
            conn.execute(
                tables.management_grants.insert().values(
                    grant_id=grant_id,
                    subject_agent_id=agent_id,
                    capability=capability.value,
                    resource_scope=resource_scope.value,
                    resource_id=resource_id,
                    owner_agent_id=None,
                    workspace=workspace,
                    granted_by_agent_id=granted_by_agent_id,
                    granted_at=now,
                    expires_at=expires_at,
                    revoked_at=None,
                )
            )
            row = conn.execute(
                sa.select(tables.management_grants).where(
                    tables.management_grants.c.grant_id == grant_id
                )
            ).fetchone()
            return _grant_from_row(row)

        return self._require_store().write_tx(_write)

    def revoke_grant(self, grant_id: str, *, actor_agent_id: str) -> ManagementGrant:
        """Revoke a management grant."""
        if actor_agent_id != "primary":
            raise PermissionError("management grant revoke requires primary actor")

        def _write(conn: Any) -> ManagementGrant:
            conn.execute(
                tables.management_grants.update()
                .where(tables.management_grants.c.grant_id == grant_id)
                .values(revoked_at=_now_iso())
            )
            row = conn.execute(
                sa.select(tables.management_grants).where(
                    tables.management_grants.c.grant_id == grant_id
                )
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown grant: {grant_id}")
            return _grant_from_row(row)

        return self._require_store().write_tx(_write)

    def can_manage(
        self,
        agent_id: str,
        capability: GrantCapability,
        resource_scope: ResourceScope,
    ) -> bool:
        """Return whether an active grant exists."""
        return any(
            grant.capability == capability and grant.resource_scope == resource_scope
            for grant in self.list_grants(agent_id)
        )

    def _update_definition(
        self,
        agent_id: str,
        *,
        expected_revision: int,
        actor_agent_id: str,
        values: dict[str, object],
    ) -> AgentDefinitionRecord:
        def _write(conn: Any) -> AgentDefinitionRecord:
            current = conn.execute(
                sa.select(tables.agent_definitions.c.revision).where(
                    tables.agent_definitions.c.agent_id == agent_id,
                    tables.agent_definitions.c.deleted_at.is_(None),
                )
            ).fetchone()
            if current is None:
                raise KeyError(f"Unknown agent: {agent_id}")
            if int(current["revision"]) != expected_revision:
                raise ValueError("agent revision conflict")
            update_values = {
                **values,
                "revision": expected_revision + 1,
                "updated_at": _now_iso(),
                "updated_by_agent_id": actor_agent_id,
            }
            conn.execute(
                tables.agent_definitions.update()
                .where(tables.agent_definitions.c.agent_id == agent_id)
                .values(**update_values)
            )
            _bump_directory_revision(conn)
            row = conn.execute(
                sa.select(tables.agent_definitions).where(
                    tables.agent_definitions.c.agent_id == agent_id
                )
            ).fetchone()
            return _definition_from_row(row)

        return self._require_store().write_tx(_write)

    def _require_active(self, agent_id: str) -> AgentDefinitionRecord:
        definition = self.get(agent_id)
        if definition is None or definition.deleted_at is not None:
            raise KeyError(f"Unknown agent: {agent_id}")
        return definition

    def _require_store(self) -> ResourceStore:
        if self._store is None:
            raise RuntimeError("AgentManager has not started")
        return self._store

    def _directory_revision(self) -> int:
        row = self._require_store().read_tx(
            lambda conn: conn.execute(
                sa.select(tables.agent_directory_meta.c.value).where(
                    tables.agent_directory_meta.c.key == "revision"
                )
            ).fetchone()
        )
        return int(row["value"]) if row is not None else 0

    def _route_status(self, agent_id: str) -> str | None:
        if self._route_status_reader is not None:
            return self._route_status_reader(agent_id)
        row = self._require_store().read_tx(
            lambda conn: conn.execute(
                sa.select(tables.agent_runtime_status.c.route_status).where(
                    tables.agent_runtime_status.c.agent_id == agent_id
                )
            ).fetchone()
        )
        return str(row["route_status"]) if row is not None and row["route_status"] else None

    def _write_runtime_status(
        self,
        agent_id: str,
        *,
        desired_state: str,
        observed_state: str,
        pid: int | None,
        route_status: str | None,
    ) -> None:
        now = _now_iso()

        def _write(conn: Any) -> None:
            existing = conn.execute(
                sa.select(tables.agent_runtime_status.c.agent_id).where(
                    tables.agent_runtime_status.c.agent_id == agent_id
                )
            ).fetchone()
            values = {
                "agent_id": agent_id,
                "desired_state": desired_state,
                "observed_state": observed_state,
                "pid": pid,
                "route_status": route_status,
                "route_seen_at": now if route_status else None,
                "updated_at": now,
            }
            if existing is None:
                conn.execute(tables.agent_runtime_status.insert().values(**values))
            else:
                conn.execute(
                    tables.agent_runtime_status.update()
                    .where(tables.agent_runtime_status.c.agent_id == agent_id)
                    .values(**values)
                )

        self._require_store().write_tx(_write)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(payload: object) -> str:
    return orjson.dumps(payload).decode("utf-8")


def _load_json(payload: str) -> dict[str, object]:
    value = orjson.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("stored JSON payload is not an object")
    return value


def _definition_from_row(row: Any) -> AgentDefinitionRecord:
    data = dict(row._mapping)
    data["identity"] = _load_json(data.pop("identity_json"))
    data["runtime"] = AgentRuntimeSpec.model_validate(_load_json(data.pop("runtime_json")))
    return AgentDefinitionRecord.model_validate(data)


def _grant_from_row(row: Any) -> ManagementGrant:
    return ManagementGrant.model_validate(dict(row._mapping))


def _bump_directory_revision(conn: Any) -> None:
    current = conn.execute(
        sa.select(tables.agent_directory_meta.c.value).where(
            tables.agent_directory_meta.c.key == "revision"
        )
    ).fetchone()
    if current is None:
        conn.execute(tables.agent_directory_meta.insert().values(key="revision", value=1))
        return
    conn.execute(
        tables.agent_directory_meta.update()
        .where(tables.agent_directory_meta.c.key == "revision")
        .values(value=int(current["value"]) + 1)
    )


class AgentHubManager:
    """In-memory Batch B Manager skeleton for definitions/runtime records."""

    def __init__(self, definitions: Iterable[AgentDefinition] = ()) -> None:
        self._definitions: dict[str, AgentDefinition] = {
            definition.id: definition for definition in definitions
        }
        self._runtime_records: dict[str, AgentRuntimeRecord] = {}

    def list_definitions(self) -> tuple[AgentDefinition, ...]:
        """Return ConfigManager-owned declarations known to this skeleton."""

        return tuple(self._definitions.values())

    def get_definition(self, agent_id: str) -> AgentDefinition | None:
        """Return one AgentDefinition, or ``None``."""

        return self._definitions.get(agent_id)

    def create_definition(
        self,
        definition: AgentDefinition,
        *,
        caller: CallerIdentity | None = None,
        capability: ManagementCapability | None = None,
    ) -> AgentDefinition:
        """Materialize a durable agent declaration and runtime record."""

        self._require_management(caller, capability, ManagementCapability.AGENT_CREATE)
        if definition.id in self._definitions:
            raise ValueError(f"AgentDefinition already exists: {definition.id}")
        self._definitions[definition.id] = definition
        self._runtime_records[definition.id] = AgentRuntimeRecord(
            agent_id=definition.id,
            runtime_kind=definition.runtime.kind,
        )
        return definition

    def delete_definition(
        self,
        agent_id: str,
        *,
        caller: CallerIdentity | None = None,
        capability: ManagementCapability | None = None,
    ) -> bool:
        """Evict one definition and its runtime-only record."""

        self._require_management(caller, capability, ManagementCapability.AGENT_DELETE)
        existed = self._definitions.pop(agent_id, None) is not None
        self._runtime_records.pop(agent_id, None)
        return existed

    def upsert_runtime_record(self, record: AgentRuntimeRecord) -> AgentRuntimeRecord:
        """Store Manager/Supervisor live state without touching definitions."""

        if record.agent_id not in self._definitions:
            raise KeyError(f"Unknown agent: {record.agent_id}")
        self._runtime_records[record.agent_id] = record
        return record

    def get_runtime_record(self, agent_id: str) -> AgentRuntimeRecord | None:
        """Return runtime-only live state, or ``None``."""

        return self._runtime_records.get(agent_id)

    def list_runtime_records(self) -> tuple[AgentRuntimeRecord, ...]:
        """Return runtime-only live states."""

        return tuple(self._runtime_records.values())

    def project_status(self, snapshot: StatusSnapshot) -> AgentRuntimeRecord:
        """Update runtime live state from a queue/status projection.

        The Manager stores the read model only.  It does not own or mutate the
        underlying session queue.
        """

        definition = self._definitions.get(snapshot.identity.agent_id)
        if definition is None:
            raise KeyError(f"Unknown agent: {snapshot.identity.agent_id}")

        current = self._runtime_records.get(snapshot.identity.agent_id)
        record = AgentRuntimeRecord(
            agent_id=snapshot.identity.agent_id,
            runtime_kind=current.runtime_kind if current else definition.runtime.kind,
            process_id=current.process_id if current else None,
            websocket_endpoint=current.websocket_endpoint if current else None,
            status=snapshot.status,
            heartbeat_at=current.heartbeat_at if current else None,
            started_at=current.started_at if current else None,
            restart_count=current.restart_count if current else 0,
            queue_depth=len(snapshot.queued_task_ids),
            active_turn_id=snapshot.active_task_id,
            last_exit_code=current.last_exit_code if current else None,
            last_error=snapshot.error or (current.last_error if current else None),
        )
        if snapshot.status is AgentStatus.idle:
            record = record.model_copy(update={"last_error": None})
        self._runtime_records[snapshot.identity.agent_id] = record
        return record

    def binding_plan(self, *, revision: int) -> BindingPlan:
        """Materialize Manager -> Access Agent platform binding plan."""

        entries: list[BindingPlanEntry] = []
        for definition in self._definitions.values():
            for binding in definition.bindings.platforms:
                entries.append(
                    BindingPlanEntry(
                        adapter_id=binding.adapter_id,
                        platform=binding.platform,
                        account_id=binding.account_id,
                        target_agent_id=definition.id,
                        enabled=binding.enabled,
                        context=binding.context,
                    )
                )
        return BindingPlan(revision=revision, entries=tuple(entries))

    def routing_snapshot(self, *, revision: int) -> RoutingSnapshot:
        """Materialize Manager -> Router read-only routing snapshot."""

        return RoutingSnapshot(
            revision=revision,
            entries=tuple(
                RoutingSnapshotEntry(
                    agent_id=definition.id,
                    native_default=definition.bindings.native_default,
                    platform_bindings=definition.bindings.platforms,
                )
                for definition in self._definitions.values()
            ),
        )

    @staticmethod
    def _require_management(
        caller: CallerIdentity | None,
        capability: ManagementCapability | None,
        required: ManagementCapability,
    ) -> None:
        """Validate management calls when a caller is supplied."""

        if caller is None and capability is None:
            return
        if caller is None or caller.kind is not CallerIdentityKind.DURABLE_AGENT:
            raise PermissionError("management requires durable agent caller identity")
        if capability is not required:
            raise PermissionError(f"management requires {required.value}")


def platform_binding(
    *,
    adapter_id: str,
    platform: str,
    target_agent_id: str,
    account_id: str | None = None,
) -> tuple[str, PlatformBinding]:
    """Small test/helper constructor that keeps imports at the Manager edge."""

    return target_agent_id, PlatformBinding(
        adapter_id=adapter_id,
        platform=platform,
        account_id=account_id,
    )
