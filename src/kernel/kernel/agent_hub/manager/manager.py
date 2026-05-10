"""Agent Hub Manager skeleton.

The Manager owns runtime state and materializes read-only plans from
ConfigManager-owned AgentDefinitions.  It does not deliver messages and does
not run agent loops.
"""

from __future__ import annotations

from collections.abc import Iterable

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
