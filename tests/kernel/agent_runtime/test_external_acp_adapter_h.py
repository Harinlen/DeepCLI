from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kernel.agents.mustang.runtime import ExternalAcpRuntimeAdapter


@pytest.mark.asyncio
async def test_external_acp_stdio_adapter_structured_prompt() -> None:
    fixture = Path(__file__).with_name("fake_acp_stdio_server.py")
    adapter = ExternalAcpRuntimeAdapter(sys.executable, [str(fixture)])

    await adapter.connect()
    try:
        initialized = await adapter.initialize()
        session_id = await adapter.new_session(cwd="/tmp")
        result = await adapter.prompt(session_id=session_id, text="ping")
        await adapter.cancel(session_id=session_id)
        closed = await adapter.close_session(session_id=session_id)
    finally:
        await adapter.close()

    assert initialized["serverInfo"]["name"] == "fake-acp"
    assert session_id == "fake-session"
    assert result.stop_reason == "end_turn"
    assert result.updates[0]["method"] == "session/update"
    assert result.updates[0]["params"]["update"]["text"] == "pong"
    assert result.updates[1]["params"]["update"]["type"] == "client_call_rejected"
    assert result.updates[1]["params"]["update"]["code"] == -32601
    assert closed == {}
