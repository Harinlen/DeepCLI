"""Probe ACP runtime backend lifecycle closure."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from kernel.agents.mustang.runtime import ExternalAcpRuntimeAdapter
from kernel.agent_hub.manager.runtime_backends import AcpRuntimeController, FakeAcpRuntime


async def main() -> None:
    runtime = FakeAcpRuntime()
    controller = AcpRuntimeController(runtime)

    print("probe=acp_runtime_backend")
    init = await controller.initialize()
    assert init["runtime"] == "fake-acp"
    print("command=initialize result=PASS runtime=fake-acp")

    created = await controller.new(cwd="/tmp")
    session_id = created["sessionId"]
    assert session_id
    print(f"command=new result=PASS session_id={session_id}")

    prompted = await controller.prompt(session_id=session_id, text="hello")
    assert prompted["success"] is True
    print("command=prompt result=PASS stop_reason=end_turn")

    permission = await controller.prompt(session_id=session_id, text="permission please")
    assert permission["success"] is True
    assert runtime.permission_requests
    print("command=permission_tunnel result=PASS method=session/request_permission")

    cancelled = await controller.cancel(session_id=session_id)
    assert cancelled["status"] == "cancelled"
    print("command=cancel result=PASS")

    status = controller.status()
    assert session_id in status["sessions"]
    print("command=status result=PASS sessions=1")

    closed = await controller.close_session(session_id=session_id)
    assert closed["success"] is True
    print("command=close_session result=PASS")

    crashed = await AcpRuntimeController(FakeAcpRuntime(crash_on_prompt=True)).prompt(
        session_id="crash",
        text="boom",
    )
    assert crashed["status"] == "failed"
    print("command=process_crash result=PASS status=failed")

    shutdown = await controller.close()
    assert shutdown["closed"] is True
    print("command=close_runtime result=PASS")

    fixture = Path("tests/kernel/agent_runtime/fake_acp_stdio_server.py").resolve()
    external = ExternalAcpRuntimeAdapter(sys.executable, [str(fixture)])
    await external.connect()
    external_controller = AcpRuntimeController(external)
    try:
        external_init = await external_controller.initialize()
        external_created = await external_controller.new(cwd="/tmp")
        external_prompt = await external_controller.prompt(
            session_id=external_created["sessionId"],
            text="ping",
        )
        assert external_init["serverInfo"]["name"] == "fake-acp"
        assert external_prompt["success"] is True
        assert external_prompt["updates"][0]["params"]["update"]["text"] == "pong"
        assert external_prompt["updates"][1]["params"]["update"]["type"] == "client_call_rejected"
        print("command=external_stdio_runtime result=PASS client_calls_fail_closed=true")
    finally:
        await external_controller.close()
    print("result=PASS")


if __name__ == "__main__":
    asyncio.run(main())
