from __future__ import annotations

from pathlib import Path

import pytest

from kernel.access_router.adapters.test_adapter import TestAccessAdapter
from kernel.access_router.control_api import AccessRouterControlAPI
from kernel.access_router.repository import AccessRouterRepository
from kernel.access_router.router import AccessRouter
from kernel.access_router.schemas import DeliverTurnRequest, RuntimeRegisterRequest

pytestmark = pytest.mark.anyio


async def test_adapter_startup_failure_records_status_and_keeps_local_path_ready(
    tmp_path: Path,
) -> None:
    router = AccessRouter(auth_token="secret", resource_home=tmp_path)
    try:
        adapter = TestAccessAdapter("test")
        adapter.fail_start = True

        async def handler(_: DeliverTurnRequest) -> dict[str, object]:
            return {"text": "local still ready"}

        router.register_runtime(_register(), handler)

        with pytest.raises(RuntimeError, match="startup failed"):
            await router.start_adapter(adapter)

        assert AccessRouterControlAPI(router).health().ready is True
        repo = AccessRouterRepository.open(tmp_path)
        try:
            assert repo.adapter_event_count("test") == 1
        finally:
            repo.close()
    finally:
        router.close()


async def test_channel_binding_resolves_target_agent(tmp_path: Path) -> None:
    router, adapter = _router_with_binding(tmp_path)
    seen: list[DeliverTurnRequest] = []
    try:
        async def handler(request: DeliverTurnRequest) -> dict[str, object]:
            seen.append(request)
            return {"text": f"reply from {request.agent_id}"}

        router.register_runtime(_register(agent_id="primary"), handler)

        result = await router.handle_adapter_inbound(
            adapter,
            adapter.inbound(channel_key="chan-1", external_message_id="m-1", text="hello"),
        )

        assert result["status"] == "completed"
        assert seen[0].agent_id == "primary"
        assert adapter.sent[0].text == "reply from primary"
    finally:
        router.close()


async def test_duplicate_platform_inbound_is_deduped(tmp_path: Path) -> None:
    router, adapter = _router_with_binding(tmp_path)
    executions = 0
    try:
        async def handler(_: DeliverTurnRequest) -> dict[str, object]:
            nonlocal executions
            executions += 1
            return {"text": f"reply-{executions}"}

        router.register_runtime(_register(), handler)
        message = adapter.inbound(channel_key="chan-1", external_message_id="m-1", text="hello")

        first = await router.handle_adapter_inbound(adapter, message)
        duplicate = await router.handle_adapter_inbound(adapter, message)

        assert first["status"] == "completed"
        assert duplicate["status"] == "duplicate"
        assert executions == 1
        assert len(adapter.sent) == 1
    finally:
        router.close()


async def test_duplicate_outbound_reply_is_suppressed(tmp_path: Path) -> None:
    router = AccessRouter(auth_token="secret", resource_home=tmp_path)
    adapter = TestAccessAdapter("test")
    try:
        first = await router.send_adapter_reply(
            adapter,
            channel_key="chan-1",
            outbound_reply_id="outbound:test:m-1",
            text="hello",
        )
        duplicate = await router.send_adapter_reply(
            adapter,
            channel_key="chan-1",
            outbound_reply_id="outbound:test:m-1",
            text="hello",
        )

        assert first is True
        assert duplicate is False
        assert len(adapter.sent) == 1
    finally:
        router.close()


def _router_with_binding(tmp_path: Path) -> tuple[AccessRouter, TestAccessAdapter]:
    router = AccessRouter(auth_token="secret", resource_home=tmp_path)
    router.declare_adapter(adapter_id="test", adapter_type="test", actor="primary")
    router.set_channel_binding(
        binding_id="binding-1",
        adapter_id="test",
        channel_key="chan-1",
        target_agent_id="primary",
        target_session_id="session-chan-1",
        actor="primary",
    )
    return router, TestAccessAdapter("test")


def _register(agent_id: str = "primary") -> RuntimeRegisterRequest:
    return RuntimeRegisterRequest(
        process_id=f"runtime-{agent_id}",
        pid=123,
        agent_id=agent_id,
        protocol_version=1,
        capabilities=("session",),
        auth_token="secret",
    )
