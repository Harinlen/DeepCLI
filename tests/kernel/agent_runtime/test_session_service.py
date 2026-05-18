from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from kernel.agents.mustang.runtime.session_service import (
    AgentSessionRuntimeService,
    CollectingRuntimeSender,
    _prompt_user_dirs,
)
from kernel.core.protocol.acp.schemas.content import AcpTextBlock
from kernel.core.protocol.acp.schemas.permission import (
    PermissionOption,
    RequestPermissionRequest,
    RequestPermissionResponse,
    ToolCallUpdate,
)
from kernel.core.protocol.acp.schemas.session import (
    CloseSessionRequest,
    ExecuteShellRequest,
    ListSessionsRequest,
    LoadSessionRequest,
    NewSessionRequest,
    PromptRequest,
    ResumeSessionRequest,
    SetSessionModeRequest,
)
from kernel.core.protocol.acp.schemas.updates import AgentMessageChunk, SessionUpdateNotification


class _DumpableResult:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def model_dump(self, *, by_alias: bool = False) -> dict[str, Any]:
        if by_alias:
            return {k.replace("_", ""): v for k, v in self.payload.items()}
        return self.payload


class _PromptResult:
    stop_reason = "end_turn"
    meta = {"trace": "ok"}


class _ExecutionResult:
    exit_code = 0
    cancelled = False


class _FakeSessionManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, Any]] = []
        self.new = AsyncMock(side_effect=self._new)
        self.list = AsyncMock(side_effect=self._list)
        self.load_session = AsyncMock(side_effect=self._load_session)
        self.prompt = AsyncMock(side_effect=self._prompt)
        self.resume_session = AsyncMock(side_effect=self._resume_session)
        self.execute_shell = AsyncMock(side_effect=self._execute_shell)
        self.set_mode = AsyncMock(side_effect=self._set_mode)
        self.close_session = AsyncMock(side_effect=self._close_session)

    async def _new(self, ctx: Any, params: Any) -> Any:
        self.calls.append(("new", ctx, params))
        return type("NewResult", (), {"session_id": "s-new"})()

    async def _list(self, ctx: Any, params: Any) -> Any:
        self.calls.append(("list", ctx, params))
        return _DumpableResult({"sessions": [], "next_cursor": params.cursor})

    async def _load_session(self, ctx: Any, params: Any) -> Any:
        self.calls.append(("load_session", ctx, params))
        await ctx.sender.notify(
            "session/update",
            SessionUpdateNotification(
                session_id=params.session_id,
                update=AgentMessageChunk(content=AcpTextBlock(type="text", text="loaded")),
            ),
        )
        return _DumpableResult({"config_options": [], "modes": {"current": "default"}})

    async def _prompt(self, ctx: Any, params: Any) -> Any:
        self.calls.append(("prompt", ctx, params))
        await ctx.sender.notify(
            "session/update",
            SessionUpdateNotification(
                session_id=params.session_id,
                update=AgentMessageChunk(content=AcpTextBlock(type="text", text="reply")),
            ),
        )
        return _PromptResult()

    async def _resume_session(self, ctx: Any, params: Any) -> Any:
        self.calls.append(("resume_session", ctx, params))
        return _DumpableResult({"config_options": [], "modes": {"current": "default"}})

    async def _execute_shell(self, ctx: Any, params: Any) -> Any:
        self.calls.append(("execute_shell", ctx, params))
        await ctx.sender.notify(
            "_mustang.agent/session/execution_update",
            _Params(session_id=params.session_id),
        )
        return _ExecutionResult()

    async def _set_mode(self, ctx: Any, params: Any) -> Any:
        self.calls.append(("set_mode", ctx, params))
        await ctx.sender.notify(
            "session/update",
            SessionUpdateNotification(
                session_id=params.session_id,
                update=AgentMessageChunk(content=AcpTextBlock(type="text", text="mode")),
            ),
        )
        return _DumpableResult({"meta": {"mode": params.mode_id}})

    async def _close_session(self, ctx: Any, params: Any) -> Any:
        self.calls.append(("close_session", ctx, params))
        return _DumpableResult({"meta": {"closed": params.session_id}})


def _service_with_manager(tmp_path: Path) -> tuple[AgentSessionRuntimeService, _FakeSessionManager]:
    service = AgentSessionRuntimeService(
        agent_id="primary",
        state_dir=tmp_path / "state",
        workspace=tmp_path,
    )
    manager = _FakeSessionManager()
    service._session_manager = manager  # type: ignore[assignment]  # unit-test seam
    return service, manager


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
async def test_collecting_runtime_sender_streams_notifications_to_runtime_client_peer() -> None:
    peer = _Peer({})
    sender = CollectingRuntimeSender(client_peer=peer)  # type: ignore[arg-type]
    notification = SessionUpdateNotification(
        session_id="s-1",
        update=AgentMessageChunk(content=AcpTextBlock(type="text", text="hello")),
    )

    await sender.notify("session/update", notification)

    assert sender.notifications == [("session/update", notification)]
    assert peer.calls == [
        {
            "method": "session/update",
            "params": {
                "sessionId": "s-1",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hello", "meta": None},
                    "meta": None,
                },
                "meta": None,
            },
            "timeout": None,
        }
    ]


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
    home_prompt_dir = home / ".deepcli" / "prompts"
    workspace_prompt_dir = workspace / ".mustang" / "prompts"
    home_prompt_dir.mkdir(parents=True)
    workspace_prompt_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("DEEPCLI_HOME", raising=False)

    assert _prompt_user_dirs(workspace) == [home_prompt_dir, workspace_prompt_dir]


def test_prompt_user_dirs_returns_none_when_no_prompt_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    assert _prompt_user_dirs(tmp_path / "workspace") is None


@pytest.mark.anyio
async def test_runtime_service_new_list_load_resume_and_close_delegate_contracts(
    tmp_path: Path,
) -> None:
    service, manager = _service_with_manager(tmp_path)

    new_result = await service.new_session(NewSessionRequest(cwd=str(tmp_path)))
    list_result = await service.list_sessions(ListSessionsRequest(cursor="next-1"))
    load_result = await service.load_session(
        LoadSessionRequest(session_id="s-new", cwd=str(tmp_path))
    )
    resume_result = await service.resume_session(
        ResumeSessionRequest(session_id="s-new", cwd=str(tmp_path))
    )
    close_result = await service.close_session(CloseSessionRequest(session_id="s-new"))

    assert new_result == {"sessionId": "s-new"}
    assert list_result == {"sessions": [], "nextcursor": "next-1"}
    assert load_result["configoptions"] == []
    assert load_result["updates"][0]["sessionId"] == "s-new"
    assert resume_result == {"configoptions": [], "modes": {"current": "default"}}
    assert close_result == {"meta": {"closed": "s-new"}}
    assert "s-new" not in service._connections
    assert [call[0] for call in manager.calls] == [
        "new",
        "list",
        "load_session",
        "resume_session",
        "close_session",
    ]
    assert manager.calls[0][2].cwd == str(tmp_path)
    assert manager.calls[1][2].cursor == "next-1"


@pytest.mark.anyio
async def test_runtime_service_prompt_collects_updates_without_runtime_peer(tmp_path: Path) -> None:
    service, manager = _service_with_manager(tmp_path)
    service._connection_for("s-1")

    result = await service.prompt(
        PromptRequest(
            session_id="s-1",
            prompt=[AcpTextBlock(type="text", text="hello")],
            meta={"mustang.agent/clientTurnId": "turn-1"},
        )
    )

    assert result["stopReason"] == "end_turn"
    assert result["_meta"] == {"trace": "ok"}
    assert result["updates"][0]["update"]["content"]["text"] == "reply"
    assert manager.calls[-1][2].meta == {"mustang.agent/clientTurnId": "turn-1"}


@pytest.mark.anyio
async def test_runtime_service_prompt_preserves_updates_when_runtime_peer_streams(
    tmp_path: Path,
) -> None:
    service, _manager = _service_with_manager(tmp_path)
    service._connection_for("s-1")
    peer = _Peer({})

    result = await service.prompt(
        PromptRequest(session_id="s-1", prompt=[AcpTextBlock(type="text", text="hello")]),
        client_peer=peer,  # type: ignore[arg-type]
    )

    assert result["updates"][0]["update"]["content"]["text"] == "reply"
    assert peer.calls[0]["method"] == "session/update"


@pytest.mark.anyio
async def test_runtime_service_execute_shell_and_set_mode_return_updates(tmp_path: Path) -> None:
    service, manager = _service_with_manager(tmp_path)
    service._connection_for("s-1")

    shell = await service.execute_shell(
        ExecuteShellRequest(session_id="s-1", command="echo hi", shell="bash")
    )
    mode = await service.set_mode(SetSessionModeRequest(session_id="s-1", mode_id="plan"))

    assert shell == {
        "exitCode": 0,
        "cancelled": False,
        "executionUpdates": [{"session_id": "s-1"}],
    }
    assert mode["meta"] == {"mode": "plan"}
    assert mode["updates"][0]["update"]["content"]["text"] == "mode"
    assert manager.calls[-2][2].command == "echo hi"
    assert manager.calls[-1][2].mode_id == "plan"
