from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from kernel.agent_runtime.session_service import (
    AgentSessionRuntimeService,
    CollectingRuntimeSender,
    _prompt_user_dirs,
)
from kernel.protocol.acp.schemas.permission import (
    PermissionOption,
    RequestPermissionRequest,
    RequestPermissionResponse,
    ToolCallUpdate,
)
from kernel.protocol.acp.schemas.updates import AgentMessageChunk
from kernel.protocol.acp.schemas.content import AcpTextBlock


class _Result(BaseModel):
    value: str


class _Params(BaseModel):
    session_id: str


class _Peer:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def request_client(
        self,
        *,
        method: str,
        params: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"method": method, "params": params, "timeout": timeout})
        return self.result


@pytest.mark.anyio
async def test_collecting_runtime_sender_records_notifications() -> None:
    sender = CollectingRuntimeSender()
    update = AgentMessageChunk(content=AcpTextBlock(type="text", text="hello"))

    await sender.notify("session/update", update)

    assert sender.notifications == [("session/update", update)]


@pytest.mark.anyio
async def test_collecting_runtime_sender_rejects_requests_without_peer() -> None:
    sender = CollectingRuntimeSender()

    with pytest.raises(RuntimeError, match="runtime client request not available: client/ask"):
        await sender.request("client/ask", _Params(session_id="s-1"), result_type=_Result)


@pytest.mark.anyio
async def test_collecting_runtime_sender_forwards_request_to_runtime_client_peer() -> None:
    peer = _Peer({"outcome": {"outcome": "selected", "optionId": "allow_once"}})
    sender = CollectingRuntimeSender(client_peer=peer)  # type: ignore[arg-type]
    params = RequestPermissionRequest(
        session_id="s-1",
        tool_call=ToolCallUpdate(tool_call_id="tool-1", title="Write file", kind="edit"),
        options=[
            PermissionOption(option_id="allow_once", name="Allow once", kind="allow_once"),
        ],
        tool_input={"path": "README.md"},
    )

    response = await sender.request(
        "session/request_permission",
        params,
        result_type=RequestPermissionResponse,
        timeout=12.5,
    )

    assert response.outcome.outcome == "selected"
    assert response.outcome.option_id == "allow_once"
    assert peer.calls == [
        {
            "method": "session/request_permission",
            "params": {
                "sessionId": "s-1",
                "toolCall": {
                    "toolCallId": "tool-1",
                    "title": "Write file",
                    "kind": "edit",
                    "inputSummary": None,
                },
                "options": [
                    {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
                ],
                "toolInput": {"path": "README.md"},
                "meta": None,
            },
            "timeout": 12.5,
        }
    ]


def test_runtime_service_manager_requires_startup(tmp_path: Path) -> None:
    service = AgentSessionRuntimeService(
        agent_id="primary",
        state_dir=tmp_path / "state",
        workspace=tmp_path,
    )

    with pytest.raises(RuntimeError, match="session runtime service is not started"):
        service._manager()


def test_runtime_service_connection_for_creates_and_reuses_bound_connection(tmp_path: Path) -> None:
    service = AgentSessionRuntimeService(
        agent_id="primary",
        state_dir=tmp_path / "state",
        workspace=tmp_path,
    )

    conn, sender = service._connection_for("s-1")
    same_conn, same_sender = service._connection_for("s-1")

    assert conn.bound_session_id == "s-1"
    assert sender is same_sender
    assert conn is same_conn
    assert conn.auth.connection_id == "agent-runtime"


def test_prompt_user_dirs_returns_only_existing_prompt_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home_prompt_dir = home / ".mustang" / "prompts"
    workspace_prompt_dir = workspace / ".mustang" / "prompts"
    home_prompt_dir.mkdir(parents=True)
    workspace_prompt_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)

    assert _prompt_user_dirs(workspace) == [home_prompt_dir, workspace_prompt_dir]


def test_prompt_user_dirs_returns_none_when_no_prompt_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    assert _prompt_user_dirs(tmp_path / "workspace") is None
