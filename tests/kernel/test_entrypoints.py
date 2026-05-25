from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kernel.agents.mustang.runtime.__main__ import _dispatch_runtime_contract, _prompt_text, _write_json
from kernel.agent_hub.contracts import HubFrame, HubFrameType


def test_kernel_main_version_prints_and_returns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from kernel import __main__ as kernel_main

    monkeypatch.setattr(sys, "argv", ["python -m kernel", "--port", "9999", "--version"])

    kernel_main.main()

    assert "deepcli kernel" in capsys.readouterr().out


def test_kernel_main_runs_uvicorn_and_sets_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    from kernel import __main__ as kernel_main

    runtime = MagicMock()
    runtime_cls = MagicMock(return_value=runtime)
    install = MagicMock()
    monkeypatch.setattr(kernel_main, "SupervisorRuntime", runtime_cls)
    monkeypatch.setattr(kernel_main, "install_signal_handlers", install)
    monkeypatch.setattr(sys, "argv", ["python -m kernel", "--port", "9999", "--dev"])

    kernel_main.main()

    config = runtime_cls.call_args.args[0]
    assert config.access_port == 9999
    assert config.dev is True
    install.assert_called_once_with(runtime)
    runtime.start.assert_called_once_with()
    runtime.wait.assert_called_once_with()
    runtime.stop.assert_called_once_with()


def test_access_agent_main_sets_router_or_compat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from kernel.agents.access import __main__ as access_main
    from kernel.uvicorn_runtime import uvicorn_loop

    run = MagicMock()
    monkeypatch.setattr(access_main, "uvicorn", MagicMock(run=run))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "access",
            "--port",
            "9001",
            "--hub-endpoint",
            "ws://hub",
            "--prompt-backend",
            "router",
            "--dev",
        ],
    )
    access_main.main()

    assert access_main.os.environ["MUSTANG_AGENT_HUB_ENDPOINT"] == "ws://hub"
    assert access_main.os.environ["MUSTANG_AGENT_PROMPT_BACKEND"] == "router"
    assert access_main.os.environ["_MUSTANG_DEV"] == "1"
    assert run.call_args.kwargs["port"] == 9001
    assert run.call_args.kwargs["loop"] == uvicorn_loop()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "access",
            "--port",
            "9002",
            "--hub-endpoint",
            "ws://hub",
            "--prompt-backend",
            "compat",
        ],
    )
    access_main.main()

    assert "MUSTANG_AGENT_PROMPT_BACKEND" not in access_main.os.environ


def test_uvicorn_loop_is_uvloop_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from kernel import uvicorn_runtime

    monkeypatch.setattr(uvicorn_runtime.sys, "platform", "linux")

    assert uvicorn_runtime.uvicorn_loop() == "uvloop"


def test_uvicorn_loop_falls_back_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from kernel import uvicorn_runtime

    monkeypatch.setattr(uvicorn_runtime.sys, "platform", "win32")

    assert uvicorn_runtime.uvicorn_loop() == "asyncio"


def test_supervisor_main_lifecycle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from kernel.supervisor import __main__ as supervisor_main

    events: list[object] = []

    class FakeRuntime:
        def __init__(self, config) -> None:
            events.append(config)

        def start(self) -> None:
            events.append("start")

        def wait(self) -> None:
            events.append("wait")

        def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr(supervisor_main, "SupervisorRuntime", FakeRuntime)
    monkeypatch.setattr(
        supervisor_main, "install_signal_handlers", lambda runtime: events.append("signals")
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "supervisor",
            "--access-port",
            "9100",
            "--state-dir",
            str(tmp_path / "state"),
            "--workspace",
            str(tmp_path / "workspace"),
            "--dev",
        ],
    )

    supervisor_main.main()

    config = events[0]
    assert config.access_port == 9100
    assert config.dev is True
    assert events[1:] == ["signals", "start", "wait", "stop"]


def test_agent_runtime_write_json_and_prompt_text(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "runtime.json"

    _write_json(path, {"b": 2, "a": 1})

    assert json.loads(path.read_text()) == {"a": 1, "b": 2}
    assert (
        _prompt_text([{"type": "text", "text": "ping"}, {"type": "image", "data": "..."}]) == "ping"
    )
    assert _prompt_text({"type": "text", "text": "nope"}) == ""


async def test_agent_runtime_dispatches_session_contracts() -> None:
    class Service:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        async def new_session(self, params):
            self.calls.append(("new", params.cwd))
            return {"sessionId": "s-new"}

        async def list_sessions(self, params):
            self.calls.append(("list", params.cwd))
            return {"sessions": []}

        async def load_session(self, params):
            self.calls.append(("load", params.session_id))
            return {"updates": []}

        async def prompt(self, params, *, client_peer=None):
            self.calls.append(("prompt", params.session_id))
            return {"stopReason": "end_turn"}

        async def resume_session(self, params):
            self.calls.append(("resume", params.session_id))
            return {"sessionId": params.session_id}

        async def cancel(self, params):
            self.calls.append(("cancel", params.session_id))

        async def close_session(self, params):
            self.calls.append(("close", params.session_id))
            return {"closed": True}

    service = Service()

    async def dispatch(contract: str, params: dict[str, object]):
        return await _dispatch_runtime_contract(
            HubFrame(
                frame_id=contract,
                frame_type=HubFrameType.REQUEST,
                contract=contract,
                payload={"params": params},
            ),
            service,  # type: ignore[arg-type]
            peer=None,
        )

    assert await dispatch("agent.session_new", {"cwd": "/tmp"}) == {
        "ok": True,
        "sessionId": "s-new",
    }
    assert await dispatch("agent.session_list", {"cwd": "/tmp"}) == {"ok": True, "sessions": []}
    assert await dispatch("agent.session_load", {"sessionId": "s1", "cwd": "/tmp"}) == {
        "ok": True,
        "updates": [],
    }
    assert await dispatch(
        "agent.prompt",
        {"sessionId": "s1", "prompt": [{"type": "text", "text": "hi"}]},
    ) == {"ok": True, "stopReason": "end_turn"}
    assert await dispatch("agent.resume", {"sessionId": "s1", "cwd": "/tmp"}) == {
        "ok": True,
        "sessionId": "s1",
    }
    assert await dispatch("agent.cancel", {"sessionId": "s1"}) == {"ok": True}
    assert await dispatch("agent.close", {"sessionId": "s1"}) == {"ok": True, "closed": True}
    assert await dispatch("agent.unknown", {}) is None

    assert [name for name, _ in service.calls] == [
        "new",
        "list",
        "load",
        "prompt",
        "resume",
        "cancel",
        "close",
    ]
