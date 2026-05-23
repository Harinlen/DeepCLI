from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from typing import Any

import pytest

from kernel.access_router.schemas import RuntimeAcpRequest
from kernel.agents.mustang.runtime import __main__ as runtime_main


def test_main_suppresses_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_keyboard_interrupt(coro: Coroutine[Any, Any, None]) -> None:
        coro.close()
        raise KeyboardInterrupt

    async def _noop() -> None:
        return None

    monkeypatch.setattr(runtime_main, "_amain", _noop)
    monkeypatch.setattr(runtime_main.asyncio, "run", _raise_keyboard_interrupt)

    runtime_main.main()


def test_main_does_not_suppress_runtime_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_runtime_error(coro: Coroutine[Any, Any, None]) -> None:
        coro.close()
        raise RuntimeError("boom")

    async def _noop() -> None:
        return None

    monkeypatch.setattr(runtime_main, "_amain", _noop)
    monkeypatch.setattr(runtime_main.asyncio, "run", _raise_runtime_error)

    with pytest.raises(RuntimeError, match="boom"):
        runtime_main.main()


def test_access_router_connection_id_accepts_snake_and_camel_case() -> None:
    assert runtime_main._access_router_connection_id(
        {"result": {"connection_id": "conn-snake"}}
    ) == "conn-snake"
    assert runtime_main._access_router_connection_id(
        {"result": {"connectionId": "conn-camel"}}
    ) == "conn-camel"


@pytest.mark.anyio
async def test_runtime_sends_router_heartbeat_notifications() -> None:
    websocket = _SendingWebSocket()
    peer = runtime_main._AccessRouterRuntimePeer(websocket)
    task = asyncio.create_task(
        runtime_main._send_router_heartbeats(
            peer,
            "conn-1",
            interval_seconds=0.01,
        )
    )
    try:
        await asyncio.wait_for(websocket.sent_event.wait(), timeout=1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    payload = json.loads(websocket.sent[0])
    assert payload == {
        "jsonrpc": "2.0",
        "method": "_mustang.router/ping",
        "params": {"connection_id": "conn-1"},
    }


@pytest.mark.anyio
async def test_runtime_peer_routes_client_response_while_dispatch_loop_keeps_reading() -> None:
    websocket = _SendingWebSocket()
    peer = runtime_main._AccessRouterRuntimePeer(websocket)

    request_task = asyncio.create_task(
        peer.request_client(method="session/request_permission", params={"sessionId": "s-1"})
    )
    await asyncio.wait_for(websocket.sent_event.wait(), timeout=1)
    request = json.loads(websocket.sent[0])

    assert request["method"] == "session/request_permission"
    assert peer.handle_client_response(
        {"jsonrpc": "2.0", "id": request["id"], "result": {"outcome": {"outcome": "cancelled"}}}
    )
    assert await asyncio.wait_for(request_task, timeout=1) == {
        "outcome": {"outcome": "cancelled"}
    }


@pytest.mark.anyio
async def test_runtime_handles_acp_initialize_inside_agent() -> None:
    result = await runtime_main._deliver_router_acp(
        RuntimeAcpRequest(
            agent_id="primary",
            method="initialize",
            params={
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "deepcli-cli", "version": "1.0.0"},
            },
        ),
        session_service=object(),  # type: ignore[arg-type]
    )

    assert result["protocolVersion"] == 1
    assert result["agentInfo"]["name"] == "mustang-agent-runtime"
    assert result["agentCapabilities"]["loadSession"] is True


@pytest.mark.anyio
async def test_runtime_handles_acp_authenticate_inside_agent() -> None:
    result = await runtime_main._deliver_router_acp(
        RuntimeAcpRequest(
            agent_id="primary",
            method="authenticate",
            params={"methodId": "none"},
        ),
        session_service=object(),  # type: ignore[arg-type]
    )

    assert result == {"meta": None}


@pytest.mark.anyio
async def test_runtime_handles_acp_activate_skill_inside_agent() -> None:
    service = _SkillService()

    result = await runtime_main._deliver_router_acp(
        RuntimeAcpRequest(
            agent_id="primary",
            method="_mustang.agent/session/activate_skill",
            params={
                "sessionId": "session-1",
                "skill": "skill-installer",
                "args": "sources",
            },
        ),
        session_service=service,  # type: ignore[arg-type]
        peer=_ClientPeer(),
    )

    assert service.seen == [("session-1", "skill-installer", "sources")]
    assert result == {"stopReason": "end_turn", "_meta": {"ok": True}, "updates": []}


class _SendingWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.sent_event = asyncio.Event()

    async def send(self, payload: str) -> None:
        self.sent.append(payload)
        self.sent_event.set()


class _SkillService:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str]] = []

    async def activate_skill(self, params: object, *, client_peer: object | None = None) -> dict:
        del client_peer
        self.seen.append((params.session_id, params.skill, params.args))
        return {"stopReason": "end_turn", "_meta": {"ok": True}, "updates": []}


class _ClientPeer:
    async def notify_client(self, *, method: str, params: dict) -> None:
        del method, params
