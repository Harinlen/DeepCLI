from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from kernel.core.protocol.interfaces.contracts.prompt_params import PromptParams
from kernel.agents.mustang.sessions.api.gateway import SessionGatewayMixin
from kernel.agents.mustang.sessions.runtime.state import Session


class _Gateway(SessionGatewayMixin):
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.created: list[dict[str, Any]] = []
        self.loaded: list[str] = []
        self.enqueued: list[dict[str, Any]] = []
        self._sessions: dict[str, Session] = {}

    async def _create_session(
        self,
        session_id: str,
        cwd: Path,
        *,
        git_branch: str | None,
        mcp_servers: list[dict[str, Any]],
    ) -> Session:
        self.created.append(
            {
                "session_id": session_id,
                "cwd": cwd,
                "git_branch": git_branch,
                "mcp_servers": mcp_servers,
            }
        )
        session = _session(session_id, self.tmp_path)
        self._sessions[session_id] = session
        return session

    async def _get_or_load(self, session_id: str) -> Session:
        self.loaded.append(session_id)
        return self._sessions[session_id]

    def _enqueue_turn(
        self,
        session: Session,
        params: PromptParams,
        *,
        request_id: str | int | None,
        text_collector: asyncio.Future[str],
        on_permission: Any,
    ) -> asyncio.Future[Any]:
        self.enqueued.append(
            {
                "session": session,
                "params": params,
                "request_id": request_id,
                "on_permission": on_permission,
            }
        )
        text_collector.set_result("assistant reply")
        response = asyncio.get_running_loop().create_future()
        response.set_result(None)
        return response


def _session(session_id: str, tmp_path: Path) -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        session_id=session_id,
        cwd=tmp_path,
        created_at=now,
        updated_at=now,
        title=None,
        git_branch=None,
        mode_id="default",
        config_options={},
        mcp_servers=[],
        orchestrator=None,  # type: ignore[arg-type]
    )


@pytest.mark.anyio
async def test_create_for_gateway_creates_home_backed_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _Gateway(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    session_id = await gateway.create_for_gateway("discord:main", "user-1")

    assert gateway.created == [
        {
            "session_id": session_id,
            "cwd": tmp_path / "home",
            "git_branch": None,
            "mcp_servers": [],
        }
    ]


@pytest.mark.anyio
async def test_run_turn_for_gateway_enqueues_prompt_and_returns_collected_text(
    tmp_path: Path,
) -> None:
    gateway = _Gateway(tmp_path)
    gateway._sessions["s-1"] = _session("s-1", tmp_path)

    async def on_permission(_request: Any) -> Any:
        return None

    result = await gateway.run_turn_for_gateway("s-1", "hello", on_permission)

    assert result == "assistant reply"
    assert gateway.loaded == ["s-1"]
    params = gateway.enqueued[0]["params"]
    assert params.session_id == "s-1"
    assert params.prompt[0].text == "hello"
    assert gateway.enqueued[0]["request_id"] is None
    assert gateway.enqueued[0]["on_permission"] is on_permission


def test_deliver_message_returns_false_for_inactive_session(tmp_path: Path) -> None:
    gateway = _Gateway(tmp_path)

    assert gateway.deliver_message("missing", "hello") is False


def test_deliver_message_buffers_reminder_with_sender_label(tmp_path: Path) -> None:
    gateway = _Gateway(tmp_path)
    gateway._sessions["target"] = _session("target", tmp_path)

    assert gateway.deliver_message("target", "hello", sender_session_id="source") is True

    assert gateway._sessions["target"].pending_reminders == [
        "Cross-session message (from session source):\nhello"
    ]
