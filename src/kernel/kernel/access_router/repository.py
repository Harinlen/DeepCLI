"""ResourceStore-backed Access Router adapter metadata."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import orjson
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kernel.core.storage import tables
from kernel.core.storage import ResourceStore


@dataclass(frozen=True, slots=True)
class ChannelBinding:
    """Resolved external channel binding."""

    binding_id: str
    adapter_id: str
    channel_key: str
    target_agent_id: str
    target_session_id: str | None


@dataclass(frozen=True, slots=True)
class IdempotencyRow:
    """Durable Access Router idempotency row."""

    key: str
    direction: str
    status: str
    result: dict[str, object] | None


class AccessRouterRepository:
    """Small repository for Access Router ResourceStore tables."""

    def __init__(self, store: ResourceStore) -> None:
        self._store = store

    @classmethod
    def open(cls, home: Path) -> "AccessRouterRepository":
        return cls(ResourceStore.open(home))

    def close(self) -> None:
        self._store.close()

    def declare_adapter(
        self,
        *,
        adapter_id: str,
        adapter_type: str,
        config: dict[str, object],
        enabled: bool = True,
        actor: str | None = None,
    ) -> int:
        payload_json = _json({"adapter_type": adapter_type, "config": config, "enabled": enabled})
        payload_hash = _hash(payload_json)
        now = _now_iso()

        def _write(conn) -> int:  # type: ignore[no-untyped-def]
            row = conn.execute(
                sa.select(tables.access_adapters.c.revision).where(
                    tables.access_adapters.c.adapter_id == adapter_id
                )
            ).fetchone()
            revision = 1 if row is None else int(row["revision"]) + 1
            conn.execute(
                sqlite_insert(tables.access_adapters)
                .values(
                    adapter_id=adapter_id,
                    adapter_type=adapter_type,
                    config_json=_json(config),
                    enabled=1 if enabled else 0,
                    revision=revision,
                    updated_at=now,
                    updated_by_agent_id=actor,
                    payload_hash=payload_hash,
                )
                .on_conflict_do_update(
                    index_elements=[tables.access_adapters.c.adapter_id],
                    set_={
                        "adapter_type": adapter_type,
                        "config_json": _json(config),
                        "enabled": 1 if enabled else 0,
                        "revision": revision,
                        "updated_at": now,
                        "updated_by_agent_id": actor,
                        "payload_hash": payload_hash,
                    },
                ),
            )
            conn.execute(
                tables.access_adapter_events.insert().values(
                    adapter_id=adapter_id,
                    event_type="adapter.declared",
                    revision=revision,
                    actor_agent_id=actor,
                    created_at=now,
                    payload_hash=payload_hash,
                )
            )
            return revision

        return self._store.write_tx(_write)

    def remove_adapter(self, adapter_id: str, *, actor: str | None = None) -> dict[str, int]:
        """Remove one adapter declaration and disable its active bindings."""
        now = _now_iso()

        def _write(conn) -> dict[str, int]:  # type: ignore[no-untyped-def]
            row = conn.execute(
                sa.select(tables.access_adapters.c.revision).where(
                    tables.access_adapters.c.adapter_id == adapter_id
                )
            ).fetchone()
            if row is None:
                raise KeyError(adapter_id)
            revision = int(row["revision"]) + 1
            result = conn.execute(
                tables.access_channel_bindings.update()
                .where(
                    tables.access_channel_bindings.c.adapter_id == adapter_id,
                    tables.access_channel_bindings.c.enabled == 1,
                )
                .values(
                    enabled=0,
                    revision=tables.access_channel_bindings.c.revision + 1,
                    updated_at=now,
                    updated_by_agent_id=actor,
                )
            )
            conn.execute(
                tables.access_adapters.delete().where(
                    tables.access_adapters.c.adapter_id == adapter_id
                )
            )
            conn.execute(
                tables.access_adapter_events.insert().values(
                    adapter_id=adapter_id,
                    event_type="adapter.deleted",
                    revision=revision,
                    actor_agent_id=actor,
                    created_at=now,
                    payload_hash=None,
                )
            )
            return {"revision": revision, "disabled_bindings": int(result.rowcount)}

        return self._store.write_tx(_write)

    def list_adapters(self) -> list[dict[str, object]]:
        """Return durable gateway declarations using internal adapter tables."""
        rows = self._store.read_tx(
            lambda conn: conn.execute(
                sa.select(
                    tables.access_adapters.c.adapter_id,
                    tables.access_adapters.c.adapter_type,
                    tables.access_adapters.c.config_json,
                    tables.access_adapters.c.enabled,
                    tables.access_adapters.c.revision,
                    tables.access_adapters.c.updated_at,
                    tables.access_adapters.c.updated_by_agent_id,
                ).order_by(tables.access_adapters.c.adapter_id)
            ).fetchall()
        )
        return [
            {
                "gateway_id": str(row["adapter_id"]),
                "adapter_id": str(row["adapter_id"]),
                "gateway_type": str(row["adapter_type"]),
                "enabled": bool(row["enabled"]),
                "revision": int(row["revision"]),
                "updated_at": str(row["updated_at"]),
                "updated_by_agent_id": row["updated_by_agent_id"],
                "config": orjson.loads(row["config_json"]),
            }
            for row in rows
        ]

    def get_adapter(self, adapter_id: str) -> dict[str, object] | None:
        matches = [row for row in self.list_adapters() if row["adapter_id"] == adapter_id]
        return matches[0] if matches else None

    def set_adapter_enabled(
        self, adapter_id: str, enabled: bool, *, actor: str | None = None
    ) -> int:
        current = self.get_adapter(adapter_id)
        if current is None:
            raise KeyError(adapter_id)
        revision = self.declare_adapter(
            adapter_id=adapter_id,
            adapter_type=str(current["gateway_type"]),
            config=dict(current["config"]) if isinstance(current["config"], dict) else {},
            enabled=enabled,
            actor=actor,
        )
        self.record_adapter_status(adapter_id, "enabled" if enabled else "disabled")
        return revision

    def set_channel_binding(
        self,
        *,
        binding_id: str,
        adapter_id: str,
        channel_key: str,
        target_agent_id: str,
        target_session_id: str | None = None,
        enabled: bool = True,
        actor: str | None = None,
    ) -> int:
        now = _now_iso()

        def _write(conn) -> int:  # type: ignore[no-untyped-def]
            row = conn.execute(
                sa.select(tables.access_channel_bindings.c.revision).where(
                    tables.access_channel_bindings.c.binding_id == binding_id
                )
            ).fetchone()
            revision = 1 if row is None else int(row["revision"]) + 1
            conn.execute(
                sqlite_insert(tables.access_channel_bindings)
                .values(
                    binding_id=binding_id,
                    adapter_id=adapter_id,
                    channel_key=channel_key,
                    target_agent_id=target_agent_id,
                    target_session_id=target_session_id,
                    enabled=1 if enabled else 0,
                    revision=revision,
                    updated_at=now,
                    updated_by_agent_id=actor,
                )
                .on_conflict_do_update(
                    index_elements=[
                        tables.access_channel_bindings.c.adapter_id,
                        tables.access_channel_bindings.c.channel_key,
                    ],
                    set_={
                        "binding_id": binding_id,
                        "target_agent_id": target_agent_id,
                        "target_session_id": target_session_id,
                        "enabled": 1 if enabled else 0,
                        "revision": revision,
                        "updated_at": now,
                        "updated_by_agent_id": actor,
                    },
                ),
            )
            return revision

        return self._store.write_tx(_write)

    def delete_channel_binding(self, binding_id: str, *, actor: str | None = None) -> None:
        now = _now_iso()

        def _write(conn) -> None:  # type: ignore[no-untyped-def]
            row = conn.execute(
                sa.select(tables.access_channel_bindings.c.adapter_id).where(
                    tables.access_channel_bindings.c.binding_id == binding_id
                )
            ).fetchone()
            if row is None:
                raise KeyError(binding_id)
            conn.execute(
                tables.access_channel_bindings.update()
                .where(tables.access_channel_bindings.c.binding_id == binding_id)
                .values(
                    enabled=0,
                    revision=tables.access_channel_bindings.c.revision + 1,
                    updated_at=now,
                    updated_by_agent_id=actor,
                )
            )

        self._store.write_tx(_write)

    def delete_bindings_for_agent_channel(
        self,
        *,
        target_agent_id: str,
        adapter_id: str | None = None,
        channel_key: str | None = None,
        actor: str | None = None,
    ) -> int:
        now = _now_iso()

        def _write(conn) -> int:  # type: ignore[no-untyped-def]
            conditions = [
                tables.access_channel_bindings.c.target_agent_id == target_agent_id,
                tables.access_channel_bindings.c.enabled == 1,
            ]
            if adapter_id is not None:
                conditions.append(tables.access_channel_bindings.c.adapter_id == adapter_id)
            if channel_key is not None:
                conditions.append(tables.access_channel_bindings.c.channel_key == channel_key)
            result = conn.execute(
                tables.access_channel_bindings.update()
                .where(sa.and_(*conditions))
                .values(
                    enabled=0,
                    revision=tables.access_channel_bindings.c.revision + 1,
                    updated_at=now,
                    updated_by_agent_id=actor,
                )
            )
            return int(result.rowcount)

        return self._store.write_tx(_write)

    def list_channel_bindings(
        self,
        *,
        adapter_id: str | None = None,
        target_agent_id: str | None = None,
        include_disabled: bool = False,
    ) -> list[dict[str, object]]:
        def _read(conn) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
            conditions = []
            if adapter_id is not None:
                conditions.append(tables.access_channel_bindings.c.adapter_id == adapter_id)
            if target_agent_id is not None:
                conditions.append(
                    tables.access_channel_bindings.c.target_agent_id == target_agent_id
                )
            if not include_disabled:
                conditions.append(tables.access_channel_bindings.c.enabled == 1)
            stmt = sa.select(
                tables.access_channel_bindings.c.binding_id,
                tables.access_channel_bindings.c.adapter_id,
                tables.access_channel_bindings.c.channel_key,
                tables.access_channel_bindings.c.target_agent_id,
                tables.access_channel_bindings.c.target_session_id,
                tables.access_channel_bindings.c.enabled,
                tables.access_channel_bindings.c.revision,
                tables.access_channel_bindings.c.updated_at,
                tables.access_channel_bindings.c.updated_by_agent_id,
            ).order_by(
                tables.access_channel_bindings.c.adapter_id,
                tables.access_channel_bindings.c.channel_key,
                tables.access_channel_bindings.c.binding_id,
            )
            if conditions:
                stmt = stmt.where(sa.and_(*conditions))
            rows = conn.execute(stmt).fetchall()
            return [
                {
                    "binding_id": str(row["binding_id"]),
                    "gateway_id": str(row["adapter_id"]),
                    "adapter_id": str(row["adapter_id"]),
                    "channel_key": str(row["channel_key"]),
                    "target_agent_id": str(row["target_agent_id"]),
                    "target_session_id": (
                        str(row["target_session_id"])
                        if row["target_session_id"] is not None
                        else None
                    ),
                    "enabled": bool(row["enabled"]),
                    "revision": int(row["revision"]),
                    "updated_at": str(row["updated_at"]),
                    "updated_by_agent_id": row["updated_by_agent_id"],
                }
                for row in rows
            ]

        return self._store.read_tx(_read)

    def resolve_binding(self, adapter_id: str, channel_key: str) -> ChannelBinding | None:
        row = self._store.read_tx(
            lambda conn: conn.execute(
                sa.select(
                    tables.access_channel_bindings.c.binding_id,
                    tables.access_channel_bindings.c.adapter_id,
                    tables.access_channel_bindings.c.channel_key,
                    tables.access_channel_bindings.c.target_agent_id,
                    tables.access_channel_bindings.c.target_session_id,
                ).where(
                    tables.access_channel_bindings.c.adapter_id == adapter_id,
                    tables.access_channel_bindings.c.channel_key == channel_key,
                    tables.access_channel_bindings.c.enabled == 1,
                )
            ).fetchone()
        )
        if row is None:
            return None
        return ChannelBinding(
            binding_id=str(row["binding_id"]),
            adapter_id=str(row["adapter_id"]),
            channel_key=str(row["channel_key"]),
            target_agent_id=str(row["target_agent_id"]),
            target_session_id=(
                str(row["target_session_id"]) if row["target_session_id"] is not None else None
            ),
        )

    def record_adapter_status(self, adapter_id: str, status: str, error: str | None = None) -> None:
        payload_hash = _hash(_json({"status": status, "error": error}))
        self._store.write_tx(
            lambda conn: conn.execute(
                tables.access_adapter_events.insert().values(
                    adapter_id=adapter_id,
                    event_type=f"adapter.{status}",
                    revision=None,
                    actor_agent_id=None,
                    created_at=_now_iso(),
                    payload_hash=payload_hash,
                )
            )
        )

    def adapter_event_count(self, adapter_id: str) -> int:
        return int(
            self._store.read_tx(
                lambda conn: conn.execute(
                    sa.select(sa.func.count().label("c")).where(
                        tables.access_adapter_events.c.adapter_id == adapter_id
                    )
                ).fetchone()["c"]
            )
        )

    def get_idempotency(self, key: str) -> IdempotencyRow | None:
        row = self._store.read_tx(
            lambda conn: conn.execute(
                sa.select(
                    tables.access_idempotency_keys.c.idempotency_key,
                    tables.access_idempotency_keys.c.direction,
                    tables.access_idempotency_keys.c.status,
                    tables.access_idempotency_keys.c.result_json,
                ).where(tables.access_idempotency_keys.c.idempotency_key == key)
            ).fetchone()
        )
        if row is None:
            return None
        result_json = row["result_json"]
        return IdempotencyRow(
            key=str(row["idempotency_key"]),
            direction=str(row["direction"]),
            status=str(row["status"]),
            result=orjson.loads(result_json) if result_json else None,
        )

    def put_idempotency(
        self,
        *,
        key: str,
        direction: str,
        adapter_id: str | None,
        external_message_id: str | None,
        internal_message_id: str,
        target_agent_id: str | None,
        status: str,
        result: dict[str, object] | None,
    ) -> None:
        now = _now_iso()
        result_json = _json(result) if result is not None else None
        self._store.write_tx(
            lambda conn: conn.execute(
                sqlite_insert(tables.access_idempotency_keys)
                .values(
                    idempotency_key=key,
                    direction=direction,
                    adapter_id=adapter_id,
                    external_message_id=external_message_id,
                    internal_message_id=internal_message_id,
                    target_agent_id=target_agent_id,
                    status=status,
                    result_json=result_json,
                    created_at=now,
                    updated_at=now,
                    expires_at=None,
                )
                .on_conflict_do_update(
                    index_elements=[tables.access_idempotency_keys.c.idempotency_key],
                    set_={
                        "status": status,
                        "result_json": result_json,
                        "updated_at": now,
                    },
                )
            )
        )

    def idempotency_count(self, prefix: str | None = None) -> int:
        if prefix is None:
            return int(
                self._store.read_tx(
                    lambda conn: conn.execute(
                        sa.select(sa.func.count().label("c")).select_from(
                            tables.access_idempotency_keys
                        )
                    ).fetchone()["c"]
                )
            )
        return int(
            self._store.read_tx(
                lambda conn: conn.execute(
                    sa.select(sa.func.count().label("c")).where(
                        tables.access_idempotency_keys.c.idempotency_key.like(f"{prefix}%")
                    )
                ).fetchone()["c"]
            )
        )


def _json(payload: object) -> str:
    return orjson.dumps(payload, option=orjson.OPT_SORT_KEYS).decode()


def _hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
