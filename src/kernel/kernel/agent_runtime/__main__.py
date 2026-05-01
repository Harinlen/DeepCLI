"""Standalone Primary Agent Runtime process entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from kernel.agent_hub import request_hub
from kernel.agent_runtime import MinimalAgentRuntimeServer
from kernel.agent_runtime.websocket_runtime import RuntimeClientPeer
from kernel.agent_runtime.session_service import AgentSessionRuntimeService
from kernel.agents import (
    AgentRegistrationRequest,
    AgentRuntimeKind,
    HubFrame,
    HubFrameType,
    RegistrationToken,
)
from kernel.protocol.acp.schemas.session import (
    CancelExecutionRequest,
    CancelNotification,
    CloseSessionRequest,
    ExecutePythonRequest,
    ExecuteShellRequest,
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
    parser.add_argument("--hub-endpoint", required=True)
    parser.add_argument("--registration-token", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--session-store-path", required=True)
    parser.add_argument("--workspace", default=str(Path.cwd()))
    parser.add_argument("--runtime-file", required=True)
    args = parser.parse_args()

    session_service = AgentSessionRuntimeService(
        agent_id=args.agent_id,
        state_dir=Path(args.state_dir),
        workspace=Path(args.workspace),
    )
    await session_service.startup()

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

    server = MinimalAgentRuntimeServer(host=args.host, port=args.port, handler=_handle_runtime_frame)
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
        args.hub_endpoint,
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
    if frame.contract == "agent.resume":
        result = await session_service.resume_session(
            ResumeSessionRequest.model_validate(frame.payload["params"])
        )
        return {"ok": True, **result}
    if frame.contract == "agent.cancel":
        await session_service.cancel(
            CancelNotification.model_validate(frame.payload["params"])
        )
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
    if frame.contract == "agent.close":
        result = await session_service.close_session(
            CloseSessionRequest.model_validate(frame.payload["params"])
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
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
