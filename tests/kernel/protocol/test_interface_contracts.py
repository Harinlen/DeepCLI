from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from datetime import datetime, timezone

from kernel.agents.access.security.context import AuthContext
from kernel.core.protocol.interfaces.client_sender import ClientSender
from kernel.core.protocol.interfaces.contracts.connection_context import ConnectionContext
from kernel.core.protocol.interfaces.contracts.remove_profile_params import RemoveProfileParams
from kernel.core.protocol.interfaces.contracts.remove_profile_result import RemoveProfileResult
from kernel.core.protocol.interfaces.event_mapper import EventMapper
from kernel.core.protocol.interfaces.handshake import Handshake


class _Params(BaseModel):
    client: str = "test"


class _Result(BaseModel):
    ok: bool = True


class _Handshake:
    async def initialize(self, conn: ConnectionContext, params: BaseModel) -> BaseModel:
        conn.initialized = True
        return _Result()

    async def authenticate(self, conn: ConnectionContext, params: BaseModel) -> BaseModel:
        return _Result()


class _Sender:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, dict[str, Any]]] = []

    async def notify(self, method: str, params: BaseModel | dict[str, Any]) -> None:
        if isinstance(params, BaseModel):
            payload = params.model_dump()
        else:
            payload = params
        self.notifications.append((method, payload))

    async def request(self, method: str, params: BaseModel | dict[str, Any]) -> Any:
        return {"method": method}


class _Mapper:
    async def map(self, event: Any, sender: ClientSender, session_id: str) -> None:
        await sender.notify("session/update", {"sessionId": session_id, "event": event})


async def test_handshake_protocol_is_runtime_checkable() -> None:
    conn = ConnectionContext(
        auth=AuthContext(
            connection_id="conn-1",
            credential_type="token",
            remote_addr="127.0.0.1:12345",
            authenticated_at=datetime.now(timezone.utc),
        )
    )
    handshake = _Handshake()

    assert isinstance(handshake, Handshake)
    result = await handshake.initialize(conn, _Params())
    auth = await handshake.authenticate(conn, _Params())

    assert conn.initialized is True
    assert result == _Result()
    assert auth == _Result()


async def test_event_mapper_protocol_is_runtime_checkable() -> None:
    mapper = _Mapper()
    sender = _Sender()

    assert isinstance(mapper, EventMapper)
    await mapper.map({"text": "hello"}, sender, "session-1")

    assert sender.notifications == [
        ("session/update", {"sessionId": "session-1", "event": {"text": "hello"}})
    ]


def test_remove_profile_contracts_are_serializable() -> None:
    params = RemoveProfileParams(name="draft-model")
    result = RemoveProfileResult()

    assert params.model_dump() == {"name": "draft-model"}
    assert result.model_dump() == {}
