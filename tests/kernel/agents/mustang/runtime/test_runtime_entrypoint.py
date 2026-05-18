from __future__ import annotations

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
