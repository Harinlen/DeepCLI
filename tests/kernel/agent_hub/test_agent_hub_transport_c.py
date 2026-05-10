from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import websockets

from kernel.agent_hub import AgentHub, AgentHubManager, AgentHubWebSocketServer, request_hub
from kernel.agents.mustang.runtime import MinimalAgentRuntimeServer, RuntimeClientPeer
from kernel.agent_hub.contracts import (
    AgentRegistrationRequest,
    AgentRuntimeKind,
    HubFrame,
    HubFrameType,
    RegistrationToken,
    RouterFrame,
    RouterFrameKind,
    RouterTarget,
    CallerIdentity,
    CallerIdentityKind,
    default_primary_agent_definition,
)

pytestmark = pytest.mark.anyio


def _registration_payload(secret: str) -> dict:
    return AgentRegistrationRequest(
        agent_id="primary",
        runtime_kind=AgentRuntimeKind.in_process_session_agent,
        websocket_endpoint="ws://127.0.0.1:9999",
        registration_token=RegistrationToken(
            token_id="token-1",
            secret=secret,
            issued_to_agent_id="primary",
        ),
    ).model_dump()


async def test_hub_internal_websocket_readiness_and_registration(tmp_path: Path) -> None:
    hub = AgentHub(
        manager=AgentHubManager(
            [default_primary_agent_definition(home=tmp_path, workspace=tmp_path)]
        )
    )
    server = AgentHubWebSocketServer(
        hub,
        registration_tokens={"primary": "secret"},
    )
    await server.start()
    try:
        readiness = await request_hub(
            server.endpoint,
            HubFrame(
                frame_id="ready-1",
                frame_type=HubFrameType.REQUEST,
                contract="hub.readiness",
            ),
        )
        assert readiness.payload["ok"] is True
        assert readiness.payload["readiness"]["schemaVersion"] == "agent-hub.b"

        registered = await request_hub(
            server.endpoint,
            HubFrame(
                frame_id="reg-1",
                frame_type=HubFrameType.REQUEST,
                contract="agent.register",
                payload=_registration_payload("secret"),
            ),
        )
        assert registered.payload == {"ok": True, "agentId": "primary"}
        assert hub.manager.get_runtime_record("primary") is not None
    finally:
        await server.stop()


async def test_hub_registration_rejects_wrong_token(tmp_path: Path) -> None:
    hub = AgentHub(
        manager=AgentHubManager(
            [default_primary_agent_definition(home=tmp_path, workspace=tmp_path)]
        )
    )
    server = AgentHubWebSocketServer(
        hub,
        registration_tokens={"primary": "secret"},
    )
    await server.start()
    try:
        response = await request_hub(
            server.endpoint,
            HubFrame(
                frame_id="reg-2",
                frame_type=HubFrameType.REQUEST,
                contract="agent.register",
                payload=_registration_payload("wrong"),
            ),
        )
        assert response.payload["ok"] is False
        assert response.payload["error"] == "PermissionError"
        assert hub.manager.get_runtime_record("primary") is None
    finally:
        await server.stop()


async def test_hub_prompt_routes_to_registered_runtime(tmp_path: Path) -> None:
    async def runtime_handler(frame: HubFrame) -> HubFrame:
        return HubFrame(
            frame_id=f"{frame.frame_id}:response",
            frame_type=HubFrameType.RESPONSE,
            contract=frame.contract,
            correlation_id=frame.frame_id,
            payload={
                "ok": True,
                "stopReason": "end_turn",
                "updates": [
                    {
                        "sessionId": frame.payload["params"]["sessionId"],
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "pong"},
                        },
                    }
                ],
            },
        )

    runtime = MinimalAgentRuntimeServer(handler=runtime_handler)
    await runtime.start()
    hub = AgentHub(
        manager=AgentHubManager(
            [default_primary_agent_definition(home=tmp_path, workspace=tmp_path)]
        )
    )
    server = AgentHubWebSocketServer(
        hub,
        registration_tokens={"primary": "secret"},
    )
    await server.start()
    try:
        payload = AgentRegistrationRequest(
            agent_id="primary",
            runtime_kind=AgentRuntimeKind.in_process_session_agent,
            websocket_endpoint=runtime.endpoint,
            registration_token=RegistrationToken(
                token_id="token-1",
                secret="secret",
                issued_to_agent_id="primary",
            ),
        ).model_dump()
        await request_hub(
            server.endpoint,
            HubFrame(
                frame_id="reg-runtime",
                frame_type=HubFrameType.REQUEST,
                contract="agent.register",
                payload=payload,
            ),
        )

        router_frame = RouterFrame(
            frame_id="prompt-route",
            kind=RouterFrameKind.USER_MESSAGE,
            source="access:native",
            target=RouterTarget(),
            caller=CallerIdentity(
                kind=CallerIdentityKind.ACCESS,
                subject_id="probe",
            ),
            session_id="session-1",
        )
        response = await request_hub(
            server.endpoint,
            HubFrame(
                frame_id="prompt-1",
                frame_type=HubFrameType.REQUEST,
                contract="agent.prompt",
                payload={
                    "routerFrame": router_frame.model_dump(),
                    "params": {
                        "sessionId": "session-1",
                        "prompt": [{"type": "text", "text": "ping"}],
                    },
                },
            ),
        )

        assert response.payload["ok"] is True
        assert response.payload["targetAgentId"] == "primary"
        assert response.payload["updates"][0]["update"]["content"]["text"] == "pong"
    finally:
        await server.stop()
        await runtime.stop()


async def test_hub_prompt_waits_for_long_running_runtime_turn(tmp_path: Path) -> None:
    async def runtime_handler(frame: HubFrame) -> HubFrame:
        await asyncio.sleep(5.2)
        return HubFrame(
            frame_id=f"{frame.frame_id}:response",
            frame_type=HubFrameType.RESPONSE,
            contract=frame.contract,
            correlation_id=frame.frame_id,
            payload={"ok": True, "stopReason": "end_turn", "updates": []},
        )

    runtime = MinimalAgentRuntimeServer(handler=runtime_handler)
    await runtime.start()
    hub = AgentHub(
        manager=AgentHubManager(
            [default_primary_agent_definition(home=tmp_path, workspace=tmp_path)]
        )
    )
    server = AgentHubWebSocketServer(
        hub,
        registration_tokens={"primary": "secret"},
    )
    await server.start()
    try:
        payload = AgentRegistrationRequest(
            agent_id="primary",
            runtime_kind=AgentRuntimeKind.in_process_session_agent,
            websocket_endpoint=runtime.endpoint,
            registration_token=RegistrationToken(
                token_id="token-1",
                secret="secret",
                issued_to_agent_id="primary",
            ),
        ).model_dump()
        await request_hub(
            server.endpoint,
            HubFrame(
                frame_id="reg-slow-runtime",
                frame_type=HubFrameType.REQUEST,
                contract="agent.register",
                payload=payload,
            ),
        )

        router_frame = RouterFrame(
            frame_id="slow-prompt-route",
            kind=RouterFrameKind.USER_MESSAGE,
            source="access:native",
            target=RouterTarget(),
            caller=CallerIdentity(
                kind=CallerIdentityKind.ACCESS,
                subject_id="probe",
            ),
            session_id="session-1",
        )
        response = await request_hub(
            server.endpoint,
            HubFrame(
                frame_id="slow-prompt-1",
                frame_type=HubFrameType.REQUEST,
                contract="agent.prompt",
                payload={
                    "routerFrame": router_frame.model_dump(),
                    "params": {
                        "sessionId": "session-1",
                        "prompt": [{"type": "text", "text": "slow"}],
                    },
                },
            ),
        )

        assert response.payload["ok"] is True
        assert response.payload["stopReason"] == "end_turn"
    finally:
        await server.stop()
        await runtime.stop()


async def test_hub_proxies_runtime_client_permission_request(tmp_path: Path) -> None:
    async def runtime_handler(frame: HubFrame, peer: RuntimeClientPeer) -> HubFrame:
        result = await peer.request_client(
            method="session/request_permission",
            params={
                "sessionId": frame.payload["params"]["sessionId"],
                "toolCall": {"toolCallId": "tool-1"},
                "options": [
                    {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"}
                ],
            },
        )
        return HubFrame(
            frame_id=f"{frame.frame_id}:response",
            frame_type=HubFrameType.RESPONSE,
            contract=frame.contract,
            correlation_id=frame.frame_id,
            payload={
                "ok": True,
                "stopReason": "end_turn",
                "updates": [],
                "permissionResult": result,
            },
        )

    runtime = MinimalAgentRuntimeServer(handler=runtime_handler)
    await runtime.start()
    hub = AgentHub(
        manager=AgentHubManager(
            [default_primary_agent_definition(home=tmp_path, workspace=tmp_path)]
        )
    )
    server = AgentHubWebSocketServer(
        hub,
        registration_tokens={"primary": "secret"},
    )
    await server.start()
    try:
        await request_hub(
            server.endpoint,
            HubFrame(
                frame_id="reg-runtime",
                frame_type=HubFrameType.REQUEST,
                contract="agent.register",
                payload=AgentRegistrationRequest(
                    agent_id="primary",
                    runtime_kind=AgentRuntimeKind.in_process_session_agent,
                    websocket_endpoint=runtime.endpoint,
                    registration_token=RegistrationToken(
                        token_id="token-1",
                        secret="secret",
                        issued_to_agent_id="primary",
                    ),
                ).model_dump(),
            ),
        )
        router_frame = RouterFrame(
            frame_id="prompt-route",
            kind=RouterFrameKind.USER_MESSAGE,
            source="access:native",
            target=RouterTarget(),
            caller=CallerIdentity(kind=CallerIdentityKind.ACCESS, subject_id="probe"),
            session_id="session-1",
        )
        async with websockets.connect(server.endpoint) as ws:
            await ws.send(
                HubFrame(
                    frame_id="prompt-1",
                    frame_type=HubFrameType.REQUEST,
                    contract="agent.prompt",
                    payload={
                        "routerFrame": router_frame.model_dump(),
                        "params": {
                            "sessionId": "session-1",
                            "prompt": [{"type": "text", "text": "needs permission"}],
                        },
                    },
                ).to_json_bytes()
            )
            raw_request = await ws.recv()
            client_request = HubFrame.from_json_bytes(raw_request)
            assert client_request.contract == "client.request"
            assert client_request.payload["method"] == "session/request_permission"
            await ws.send(
                HubFrame(
                    frame_id=f"{client_request.frame_id}:response",
                    frame_type=HubFrameType.RESPONSE,
                    contract=client_request.contract,
                    correlation_id=client_request.frame_id,
                    payload={
                        "ok": True,
                        "result": {
                            "outcome": {
                                "outcome": "selected",
                                "optionId": "allow_once",
                            }
                        },
                    },
                ).to_json_bytes()
            )
            raw_response = await ws.recv()
            response = HubFrame.from_json_bytes(raw_response)

        assert response.payload["ok"] is True
        assert response.payload["permissionResult"]["outcome"]["optionId"] == "allow_once"
    finally:
        await server.stop()
        await runtime.stop()
