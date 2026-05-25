"""Probe fake platform inbound routing through Access Router bindings."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from kernel.access_router.adapters.test_adapter import TestAccessAdapter
from kernel.access_router.router import AccessRouter
from kernel.access_router.schemas import DeliverTurnRequest, RuntimeRegisterRequest


def _register(agent_id: str = "primary") -> RuntimeRegisterRequest:
    return RuntimeRegisterRequest(
        process_id=f"runtime-{agent_id}",
        pid=123,
        agent_id=agent_id,
        protocol_version=1,
        capabilities=("session",),
        auth_token="secret",
    )


async def main() -> None:
    with TemporaryDirectory() as tmp:
        router = AccessRouter(auth_token="secret", resource_home=Path(tmp))
        adapter = TestAccessAdapter("testgw")
        seen: list[DeliverTurnRequest] = []
        try:
            router.declare_adapter(adapter_id="testgw", adapter_type="test", actor="primary")
            router.set_channel_binding(
                binding_id="binding-1",
                adapter_id="testgw",
                channel_key="chan-1",
                target_agent_id="primary",
                target_session_id="session-chan-1",
                actor="primary",
            )

            async def handler(request: DeliverTurnRequest) -> dict[str, object]:
                seen.append(request)
                return {"text": f"reply:{request.prompt}", "agent": request.agent_id}

            router.register_runtime(_register(), handler)
            message = adapter.inbound(channel_key="chan-1", external_message_id="m-1", text="hello")

            print("probe=platform_agent_routing")
            first = await router.handle_adapter_inbound(adapter, message)
            assert first["status"] == "completed"
            assert seen[0].agent_id == "primary"
            assert seen[0].session_id == "session-chan-1"
            assert adapter.sent[0].text == "reply:hello"
            print("command=fake_inbound result=PASS target_agent=primary session=session-chan-1")
            print("command=reply_sink result=PASS replies=1")

            duplicate = await router.handle_adapter_inbound(adapter, message)
            assert duplicate["status"] == "duplicate"
            assert len(seen) == 1
            assert len(adapter.sent) == 1
            print("command=duplicate_idempotency result=PASS executions=1 replies=1")

            missing = adapter.inbound(channel_key="missing", external_message_id="m-2", text="hello")
            try:
                await router.handle_adapter_inbound(adapter, missing)
            except Exception as exc:
                assert "no enabled binding" in str(exc)
                print("command=missing_binding result=PASS error=route_unavailable")
            else:
                raise AssertionError("missing binding should fail")

            print("result=PASS")
        finally:
            router.close()


if __name__ == "__main__":
    asyncio.run(main())
