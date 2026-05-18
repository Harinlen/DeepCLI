"""Standalone Mustang Agent runtime process entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import websockets
from kernel.agent_hub import request_hub
from kernel.agents.mustang.runtime import MinimalAgentRuntimeServer
from kernel.agents.mustang.runtime.websocket_runtime import RuntimeClientPeer
from kernel.agents.mustang.runtime.session_service import AgentSessionRuntimeService
from kernel.access_router.schemas import RuntimeAcpRequest, RuntimeRegisterRequest
from kernel.agent_hub.contracts import (
    AgentRegistrationRequest,
    AgentRuntimeKind,
    HubFrame,
    HubFrameType,
    RegistrationToken,
)
from kernel.core.protocol.acp.schemas.session import (
    ActivateSkillRequest,
    CancelExecutionRequest,
    CancelNotification,
    CloseSessionRequest,
    ExecutePythonRequest,
    ExecuteShellRequest,
    GetUsageRequest,
    ListSessionsRequest,
    LoadSessionRequest,
    NewSessionRequest,
    PromptRequest,
    ResumeSessionRequest,
    SetSessionModeRequest,
)


async def _amain() -> None:
    parser = argparse.ArgumentParser(description="Mustang Agent Runtime")
    parser.add_argument("--agent-id", default="primary")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--hub-endpoint")
    parser.add_argument("--access-router-endpoint")
    parser.add_argument("--registration-token", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--session-store-path", required=True)
    parser.add_argument("--workspace", default=str(Path.cwd()))
    parser.add_argument("--runtime-file", required=True)
    parser.add_argument("--supervisor-control-socket")
    parser.add_argument("--supervisor-control-token")
    args = parser.parse_args()

    if args.supervisor_control_socket:
        os.environ["MUSTANG_SUPERVISOR_CONTROL_SOCKET"] = args.supervisor_control_socket
    if args.supervisor_control_token:
        os.environ["MUSTANG_SUPERVISOR_CONTROL_TOKEN"] = args.supervisor_control_token
    os.environ["MUSTANG_AGENT_ID"] = args.agent_id

    session_service = AgentSessionRuntimeService(
        agent_id=args.agent_id,
        state_dir=Path(args.state_dir),
        workspace=Path(args.workspace),
    )
    await session_service.startup()

    if args.access_router_endpoint:
        try:
            await _run_access_router_client(args, session_service)
        finally:
            await session_service.shutdown()
        return

    async def _handle_runtime_frame(frame: HubFrame, peer: RuntimeClientPeer) -> HubFrame:
        try:
            payload = await _dispatch_runtime_contract(frame, session_service, peer)
        except Exception as exc:
            return HubFrame(
                frame_id=f"{frame.frame_id}:error",
                frame_type=HubFrameType.RESPONSE,
                contract=frame.contract,
                correlation_id=frame.frame_id,
                payload={
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
            )
        if payload is not None:
            return HubFrame(
                frame_id=f"{frame.frame_id}:response",
                frame_type=HubFrameType.RESPONSE,
                contract=frame.contract,
                correlation_id=frame.frame_id,
                payload=payload,
            )
        if frame.contract == "agent.prompt":
            text = _prompt_text(frame.payload.get("prompt", []))
            answer = "pong" if text.strip().lower() == "ping" else f"received: {text}"
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
                            "sessionId": frame.payload.get("sessionId"),
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": answer},
                                "meta": {
                                    "mustang.agent/runtime": {
                                        "agentId": args.agent_id,
                                        "backend": "minimal-agent-runtime",
                                    }
                                },
                            },
                        }
                    ],
                },
            )
        return HubFrame(
            frame_id=f"{frame.frame_id}:response",
            frame_type=HubFrameType.RESPONSE,
            contract=frame.contract,
            correlation_id=frame.frame_id,
            payload={"ok": True},
        )

    server = MinimalAgentRuntimeServer(
        host=args.host, port=args.port, handler=_handle_runtime_frame
    )
    await server.start()
    request = AgentRegistrationRequest(
        agent_id=args.agent_id,
        runtime_kind=AgentRuntimeKind.in_process_session_agent,
        websocket_endpoint=server.endpoint,
        capabilities=("session", "prompt"),
        registration_token=RegistrationToken(
            token_id=f"{args.agent_id}-supervisor-token",
            secret=args.registration_token,
            issued_to_agent_id=args.agent_id,
        ),
    )
    response = await request_hub(
        str(args.hub_endpoint),
        HubFrame(
            frame_id=f"{args.agent_id}-register",
            frame_type=HubFrameType.REQUEST,
            contract="agent.register",
            payload=request.model_dump(),
        ),
    )
    if response.payload.get("ok") is not True:
        raise SystemExit(f"registration failed: {response.payload}")
    _write_json(
        Path(args.runtime_file),
        {
            "pid": os.getpid(),
            "agentId": args.agent_id,
            "endpoint": server.endpoint,
            "stateDir": args.state_dir,
            "sessionStorePath": args.session_store_path,
            "registered": True,
            "role": "primary_agent",
        },
    )
    try:
        await asyncio.Event().wait()
    finally:
        await server.stop()
        await session_service.shutdown()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


class _AccessRouterRuntimePeer:
    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._counter = 0

    async def request_client(
        self,
        *,
        method: str,
        params: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self._counter += 1
        request_id = f"runtime-client-{self._counter}"
        await self._ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
        )
        if timeout is None:
            raw = await self._ws.recv()
        else:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        response = json.loads(raw)
        if response.get("id") != request_id:
            raise RuntimeError("client request response id mismatch")
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    async def notify_client(self, *, method: str, params: dict[str, Any]) -> None:
        await self._ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                }
            )
        )


async def _run_access_router_client(
    args: argparse.Namespace,
    session_service: AgentSessionRuntimeService,
) -> None:
    endpoint = str(args.access_router_endpoint).replace("http://", "ws://").replace("https://", "wss://")
    runtime_url = endpoint.rstrip("/") + "/runtime"
    while True:
        try:
            async with websockets.connect(runtime_url) as ws:
                register = RuntimeRegisterRequest(
                    process_id=f"{args.agent_id}-{os.getpid()}",
                    pid=os.getpid(),
                    agent_id=args.agent_id,
                    protocol_version=1,
                    capabilities=("session", "acp"),
                    auth_token=args.registration_token,
                    role="agent_runtime",
                )
                await ws.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "register",
                            "method": "_mustang.router/register_runtime",
                            "params": register.model_dump(),
                        }
                    )
                )
                ack = json.loads(await ws.recv())
                if ack.get("ok") is not True and ack.get("result", {}).get("ok") is not True:
                    raise RuntimeError(f"registration failed: {ack}")
                _write_json(
                    Path(args.runtime_file),
                    {
                        "pid": os.getpid(),
                        "agentId": args.agent_id,
                        "endpoint": runtime_url,
                        "stateDir": args.state_dir,
                        "sessionStorePath": args.session_store_path,
                        "registered": True,
                        "role": "primary_agent",
                    },
                )
                peer = _AccessRouterRuntimePeer(ws)
                async for raw in ws:
                    request = json.loads(raw)
                    request_id = request.get("id")
                    try:
                        if request.get("method") == "_mustang.runtime/request":
                            acp = RuntimeAcpRequest.model_validate(request.get("params", {}))
                            result = await _deliver_router_acp(acp, session_service, peer)
                        elif request.get("method") == "_mustang.runtime/ping":
                            result = {"ok": True}
                        else:
                            result = {"ok": False, "error": "unknown_runtime_method"}
                        await ws.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}))
                    except Exception as exc:
                        code: str | int = -32601 if isinstance(exc, ValueError) else type(exc).__name__
                        await ws.send(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": request_id,
                                    "error": {"code": code, "message": str(exc)},
                                }
                            )
                        )
        except (OSError, websockets.ConnectionClosed):
            await asyncio.sleep(0.2)


async def _deliver_router_acp(
    request: RuntimeAcpRequest,
    session_service: AgentSessionRuntimeService,
    peer: _AccessRouterRuntimePeer | None = None,
) -> dict[str, object]:
    method = request.method
    params = request.params
    if method == "initialize":
        return {
            "protocolVersion": 1,
            "agentInfo": {"name": "mustang-agent-runtime"},
            "agentCapabilities": {"loadSession": True},
            "promptCapabilities": {
                "image": True,
                "audio": False,
                "embeddedContext": True,
            },
            "mcpCapabilities": {"http": True, "sse": True},
            "sessionCapabilities": {"load": True, "resume": True, "cancel": True},
            "_meta": {
                "mustang.agent/extensions": [
                    "commands",
                    "config",
                    "model",
                    "secrets",
                    "session-exec",
                    "tool-snapshot",
                ]
            },
        }
    if method == "authenticate":
        return {"meta": None}
    if method == "session/new":
        return await session_service.new_session(NewSessionRequest.model_validate(params))
    if method == "session/list":
        return _camelize(await session_service.list_sessions(ListSessionsRequest.model_validate(params)))
    if method == "session/load":
        result = _camelize(await session_service.load_session(LoadSessionRequest.model_validate(params)))
        for update in result.get("updates", []):
            if isinstance(update, dict):
                if peer is None:
                    raise RuntimeError("runtime peer is required for session/load updates")
                await peer.notify_client(method="session/update", params=update)
        return result
    if method == "session/resume":
        return _camelize(await session_service.resume_session(ResumeSessionRequest.model_validate(params)))
    if method == "session/close":
        return await session_service.close_session(CloseSessionRequest.model_validate(params))
    if method == "session/prompt":
        if peer is None:
            raise RuntimeError("runtime peer is required for session/prompt")
        return await session_service.prompt(PromptRequest.model_validate(params), client_peer=peer)  # type: ignore[arg-type]
    if method == "session/set_mode":
        return await session_service.set_mode(SetSessionModeRequest.model_validate(params))
    if method == "_mustang.agent/session/execute_shell":
        if peer is None:
            raise RuntimeError("runtime peer is required for session/execute_shell")
        result = await session_service.execute_shell(ExecuteShellRequest.model_validate(params))
        for update in result.get("executionUpdates", []):
            if isinstance(update, dict):
                await peer.notify_client(
                    method="_mustang.agent/session/execution_update",
                    params=update,
                )
        return result
    if method == "_mustang.agent/session/execute_python":
        if peer is None:
            raise RuntimeError("runtime peer is required for session/execute_python")
        result = await session_service.execute_python(ExecutePythonRequest.model_validate(params))
        for update in result.get("executionUpdates", []):
            if isinstance(update, dict):
                await peer.notify_client(
                    method="_mustang.agent/session/execution_update",
                    params=update,
                )
        return result
    if method == "_mustang.agent/commands/list":
        return await session_service.commands_list()
    if method == "_mustang.agent/session/get_usage":
        return await session_service.get_usage(GetUsageRequest.model_validate(params))
    if method.startswith("_mustang.agent/model/"):
        return await session_service.model_request(method, params)
    if method == "_mustang.agent/secrets/auth":
        from kernel.core.protocol.acp.routing import REQUEST_DISPATCH

        if session_service.module_table is None:
            raise RuntimeError("session runtime service is not started")
        spec = REQUEST_DISPATCH[method]
        handler = session_service.module_table.secrets
        request_params = spec.params_type.model_validate(params)
        result = await spec.handler(handler, None, request_params)  # type: ignore[arg-type]
        return result.model_dump(by_alias=True)
    if method.startswith("_mustang.agent/") or method.startswith("_mustang.gateways/"):
        return await session_service.tools_request(method, params)
    raise ValueError(f"unsupported ACP method: {method}")


def _camelize(value: Any) -> Any:
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    if isinstance(value, dict):
        return {_camel_key(str(key)): _camelize(item) for key, item in value.items()}
    return value


def _camel_key(key: str) -> str:
    if "_" not in key:
        return key
    head, *tail = key.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


async def _dispatch_runtime_contract(
    frame: HubFrame,
    session_service: AgentSessionRuntimeService,
    peer: RuntimeClientPeer | None = None,
) -> dict[str, object] | None:
    if frame.contract == "agent.session_new":
        result = await session_service.new_session(
            NewSessionRequest.model_validate(frame.payload["params"])
        )
        return {"ok": True, **result}
    if frame.contract == "agent.session_list":
        result = await session_service.list_sessions(
            ListSessionsRequest.model_validate(frame.payload["params"])
        )
        return {"ok": True, **result}
    if frame.contract == "agent.session_load":
        result = await session_service.load_session(
            LoadSessionRequest.model_validate(frame.payload["params"])
        )
        return {"ok": True, **result}
    if frame.contract == "agent.prompt":
        result = await session_service.prompt(
            PromptRequest.model_validate(frame.payload["params"]),
            client_peer=peer,
        )
        return {"ok": True, **result}
    if frame.contract == "agent.activate_skill":
        result = await session_service.activate_skill(
            ActivateSkillRequest.model_validate(frame.payload["params"]),
            client_peer=peer,
        )
        return {"ok": True, **result}
    if frame.contract == "agent.commands_list":
        result = await session_service.commands_list()
        return {"ok": True, **result}
    if frame.contract == "agent.resume":
        result = await session_service.resume_session(
            ResumeSessionRequest.model_validate(frame.payload["params"])
        )
        return {"ok": True, **result}
    if frame.contract == "agent.cancel":
        await session_service.cancel(CancelNotification.model_validate(frame.payload["params"]))
        return {"ok": True}
    if frame.contract == "agent.execute_shell":
        result = await session_service.execute_shell(
            ExecuteShellRequest.model_validate(frame.payload["params"])
        )
        return {"ok": True, **result}
    if frame.contract == "agent.execute_python":
        result = await session_service.execute_python(
            ExecutePythonRequest.model_validate(frame.payload["params"])
        )
        return {"ok": True, **result}
    if frame.contract == "agent.cancel_execution":
        await session_service.cancel_execution(
            CancelExecutionRequest.model_validate(frame.payload["params"])
        )
        return {"ok": True}
    if frame.contract == "agent.set_mode":
        result = await session_service.set_mode(
            SetSessionModeRequest.model_validate(frame.payload["params"])
        )
        return {"ok": True, **result}
    if frame.contract == "agent.get_usage":
        result = await session_service.get_usage(
            GetUsageRequest.model_validate(frame.payload["params"])
        )
        return {"ok": True, **result}
    if frame.contract == "agent.close":
        result = await session_service.close_session(
            CloseSessionRequest.model_validate(frame.payload["params"])
        )
        return {"ok": True, **result}
    if frame.contract == "agent.model_request":
        payload = dict(frame.payload.get("params", {}))
        result = await session_service.model_request(
            str(payload.get("method", "")),
            dict(payload.get("params", {})),
        )
        return {"ok": True, **result}
    if frame.contract == "agent.tools_request":
        payload = dict(frame.payload.get("params", {}))
        result = await session_service.tools_request(
            str(payload.get("method", "")),
            dict(payload.get("params", {})),
        )
        return {"ok": True, **result}
    return None


def _prompt_text(prompt: object) -> str:
    if not isinstance(prompt, list):
        return ""
    parts: list[str] = []
    for block in prompt:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
