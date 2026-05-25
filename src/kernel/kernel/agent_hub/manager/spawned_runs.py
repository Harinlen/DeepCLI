"""ResourceStore-backed spawned Agent run registry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import orjson
import sqlalchemy as sa

from kernel.core.storage import ResourceStore, tables


@dataclass(frozen=True)
class SpawnedRunRecord:
    run_id: str
    parent_session_id: str
    requester_agent_id: str
    target_agent_id: str
    runtime: str
    mode: str
    session_id: str
    status: str
    task: str | None
    last_message: str | None
    result: dict[str, Any] | None
    provenance: dict[str, Any] | None
    binding_id: str | None
    timeout_seconds: int | None
    wait_mode: str | None
    reply_back_enabled: bool
    announce_enabled: bool
    acp_session_id: str | None
    created_at: str
    updated_at: str
    stopped_at: str | None
    revision: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "parentSessionId": self.parent_session_id,
            "requesterAgentId": self.requester_agent_id,
            "targetAgentId": self.target_agent_id,
            "runtime": self.runtime,
            "mode": self.mode,
            "sessionId": self.session_id,
            "status": self.status,
            "task": self.task,
            "lastMessage": self.last_message,
            "result": self.result,
            "provenance": self.provenance,
            "bindingId": self.binding_id,
            "timeoutSeconds": self.timeout_seconds,
            "waitMode": self.wait_mode,
            "replyBackEnabled": self.reply_back_enabled,
            "announceEnabled": self.announce_enabled,
            "acpSessionId": self.acp_session_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "stoppedAt": self.stopped_at,
            "revision": self.revision,
        }


class SpawnedRunRegistry:
    """Durable registry for caller-owned spawned runs."""

    def __init__(self, store: ResourceStore) -> None:
        self._store = store

    @classmethod
    def open(cls, home: Path) -> "SpawnedRunRegistry":
        return cls(ResourceStore.open(home))

    def close(self) -> None:
        self._store.close()

    def spawn(
        self,
        *,
        parent_session_id: str,
        requester_agent_id: str,
        target_agent_id: str,
        runtime: str,
        mode: str,
        task: str,
        binding_id: str | None = None,
        timeout_seconds: int | None = None,
        wait_mode: str | None = None,
        reply_back_enabled: bool = False,
        announce_enabled: bool = False,
        acp_session_id: str | None = None,
    ) -> SpawnedRunRecord:
        now = _now_iso()
        run_id = f"run-{uuid4().hex}"
        session_id = f"agent:{target_agent_id}:subagent:{run_id}"
        payload = {
            "run_id": run_id,
            "parent_session_id": parent_session_id,
            "requester_agent_id": requester_agent_id,
            "target_agent_id": target_agent_id,
            "runtime": runtime,
            "mode": mode,
            "session_id": session_id,
            "status": "running",
            "task": task,
            "last_message": task,
            "result_json": None,
            "provenance_json": orjson.dumps(
                {
                    "kind": "inter_session",
                    "requesterAgentId": requester_agent_id,
                    "targetAgentId": target_agent_id,
                    "parentSessionId": parent_session_id,
                }
            ).decode(),
            "binding_id": binding_id,
            "timeout_seconds": timeout_seconds,
            "wait_mode": wait_mode,
            "reply_back_enabled": 1 if reply_back_enabled else 0,
            "announce_enabled": 1 if announce_enabled else 0,
            "acp_session_id": acp_session_id,
            "created_at": now,
            "updated_at": now,
            "stopped_at": None,
            "revision": 1,
        }

        def _write(conn: Any) -> SpawnedRunRecord:
            conn.execute(tables.agent_spawned_runs.insert().values(**payload))
            _event(
                conn,
                run_id,
                "spawned",
                1,
                requester_agent_id,
                {
                    "task": task,
                    "bindingId": binding_id,
                    "waitMode": wait_mode,
                    "replyBackEnabled": reply_back_enabled,
                    "announceEnabled": announce_enabled,
                    "acpSessionId": acp_session_id,
                },
            )
            row = conn.execute(
                sa.select(tables.agent_spawned_runs).where(
                    tables.agent_spawned_runs.c.run_id == run_id
                )
            ).fetchone()
            return _record(row)

        return self._store.write_tx(_write)

    def list(self, *, requester_agent_id: str) -> list[SpawnedRunRecord]:
        rows = self._store.read_tx(
            lambda conn: conn.execute(
                sa.select(tables.agent_spawned_runs)
                .where(tables.agent_spawned_runs.c.requester_agent_id == requester_agent_id)
                .order_by(tables.agent_spawned_runs.c.created_at)
            ).fetchall()
        )
        return [_record(row) for row in rows]

    def get_owned(self, run_id: str, *, requester_agent_id: str) -> SpawnedRunRecord | None:
        row = self._store.read_tx(
            lambda conn: conn.execute(
                sa.select(tables.agent_spawned_runs).where(
                    tables.agent_spawned_runs.c.run_id == run_id,
                    tables.agent_spawned_runs.c.requester_agent_id == requester_agent_id,
                )
            ).fetchone()
        )
        return _record(row) if row is not None else None

    def update_message(
        self,
        run_id: str,
        *,
        requester_agent_id: str,
        message: str,
    ) -> SpawnedRunRecord:
        return self._update(
            run_id,
            requester_agent_id=requester_agent_id,
            values={"last_message": message, "status": "running"},
            event_type="message",
            event_payload={"message": message},
        )

    def complete(
        self,
        run_id: str,
        *,
        requester_agent_id: str,
        result: dict[str, Any],
    ) -> SpawnedRunRecord:
        return self._update(
            run_id,
            requester_agent_id=requester_agent_id,
            values={"status": "completed", "result_json": orjson.dumps(result).decode()},
            event_type="completed",
            event_payload={"result": result},
        )

    def fail(
        self,
        run_id: str,
        *,
        requester_agent_id: str,
        error: str,
    ) -> SpawnedRunRecord:
        return self._update(
            run_id,
            requester_agent_id=requester_agent_id,
            values={
                "status": "failed",
                "result_json": orjson.dumps({"success": False, "error": error}).decode(),
            },
            event_type="failed",
            event_payload={"error": error},
        )

    def stop(self, run_id: str, *, requester_agent_id: str) -> SpawnedRunRecord:
        return self._update(
            run_id,
            requester_agent_id=requester_agent_id,
            values={"status": "stopped", "stopped_at": _now_iso()},
            event_type="stopped",
            event_payload={},
        )

    def steer(self, run_id: str, *, requester_agent_id: str, message: str) -> SpawnedRunRecord:
        return self.update_message(run_id, requester_agent_id=requester_agent_id, message=message)

    def events(self, run_id: str, *, requester_agent_id: str) -> list[dict[str, Any]]:
        owned = self.get_owned(run_id, requester_agent_id=requester_agent_id)
        if owned is None:
            return []
        rows = self._store.read_tx(
            lambda conn: conn.execute(
                sa.select(tables.agent_spawned_run_events)
                .where(tables.agent_spawned_run_events.c.run_id == run_id)
                .order_by(tables.agent_spawned_run_events.c.revision)
            ).fetchall()
        )
        return [
            {
                "eventType": str(row["event_type"]),
                "revision": int(row["revision"]),
                "actorAgentId": row["actor_agent_id"],
                "createdAt": str(row["created_at"]),
                "payload": orjson.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def _update(
        self,
        run_id: str,
        *,
        requester_agent_id: str,
        values: dict[str, Any],
        event_type: str,
        event_payload: dict[str, Any],
    ) -> SpawnedRunRecord:
        now = _now_iso()

        def _write(conn: Any) -> SpawnedRunRecord:
            row = conn.execute(
                sa.select(tables.agent_spawned_runs).where(
                    tables.agent_spawned_runs.c.run_id == run_id,
                    tables.agent_spawned_runs.c.requester_agent_id == requester_agent_id,
                )
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            revision = int(row["revision"]) + 1
            conn.execute(
                tables.agent_spawned_runs.update()
                .where(tables.agent_spawned_runs.c.run_id == run_id)
                .values(**values, updated_at=now, revision=revision)
            )
            _event(conn, run_id, event_type, revision, requester_agent_id, event_payload)
            updated = conn.execute(
                sa.select(tables.agent_spawned_runs).where(
                    tables.agent_spawned_runs.c.run_id == run_id
                )
            ).fetchone()
            return _record(updated)

        return self._store.write_tx(_write)


def _event(
    conn: Any,
    run_id: str,
    event_type: str,
    revision: int,
    actor_agent_id: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        tables.agent_spawned_run_events.insert().values(
            run_id=run_id,
            event_type=event_type,
            revision=revision,
            actor_agent_id=actor_agent_id,
            created_at=_now_iso(),
            payload_json=orjson.dumps(payload).decode(),
        )
    )


def _record(row: Any) -> SpawnedRunRecord:
    return SpawnedRunRecord(
        run_id=str(row["run_id"]),
        parent_session_id=str(row["parent_session_id"]),
        requester_agent_id=str(row["requester_agent_id"]),
        target_agent_id=str(row["target_agent_id"]),
        runtime=str(row["runtime"]),
        mode=str(row["mode"]),
        session_id=str(row["session_id"]),
        status=str(row["status"]),
        task=row["task"],
        last_message=row["last_message"],
        result=_json_or_none(row["result_json"]),
        provenance=_json_or_none(row["provenance_json"]),
        binding_id=row["binding_id"],
        timeout_seconds=(
            int(row["timeout_seconds"]) if row["timeout_seconds"] is not None else None
        ),
        wait_mode=row["wait_mode"],
        reply_back_enabled=bool(row["reply_back_enabled"]),
        announce_enabled=bool(row["announce_enabled"]),
        acp_session_id=row["acp_session_id"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        stopped_at=row["stopped_at"],
        revision=int(row["revision"]),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_or_none(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    parsed = orjson.loads(value)
    return parsed if isinstance(parsed, dict) else None
