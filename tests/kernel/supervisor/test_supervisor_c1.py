from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

import kernel.supervisor.control as supervisor_control
import kernel.supervisor.runtime as supervisor_runtime
from kernel.supervisor import ChildKernelLaunch, SupervisorConfig, SupervisorRuntime
from kernel.supervisor.runtime import (
    ChildSpec,
    _wait_default_route,
    _wait_http_readiness,
    _wait_runtime_file,
    _write_json,
    install_signal_handlers,
)
from kernel.supervisor.control import (
    SupervisorControlConfig,
    SupervisorControlServer,
    request_control,
)
from kernel.supervisor.child_kernel import build_child_kernel_spec


def test_supervisor_builds_ordered_child_specs(tmp_path: Path) -> None:
    runtime = SupervisorRuntime(
        SupervisorConfig(
            access_port=8330,
            state_dir=tmp_path / "state",
            workspace=tmp_path,
        )
    )

    specs = runtime.build_specs()

    assert list(specs) == ["hub", "access", "primary"]
    assert specs["hub"].command[2] == "kernel.agent_hub"
    assert specs["access"].command[2] == "kernel.agents.access"
    assert specs["primary"].command[2] == "kernel.agents.mustang.runtime"
    assert "--prompt-backend" in specs["access"].command
    assert "router" in specs["access"].command
    assert f"--primary-token={runtime.primary_token}" in specs["hub"].command
    assert f"--registration-token={runtime.primary_token}" in specs["primary"].command


def test_supervisor_runtime_file_marks_stopped(tmp_path: Path) -> None:
    runtime = SupervisorRuntime(
        SupervisorConfig(
            access_port=8331,
            state_dir=tmp_path / "state",
            workspace=tmp_path,
        )
    )

    runtime._write_stopped_runtime_file()

    payload = json.loads(runtime.config.runtime_file.read_text(encoding="utf-8"))
    assert payload["ready"] is False
    assert payload["stopped"] is True


def test_supervisor_runtime_file_includes_child_status(tmp_path: Path) -> None:
    runtime = SupervisorRuntime(
        SupervisorConfig(
            access_port=8331,
            state_dir=tmp_path / "state",
            workspace=tmp_path,
        )
    )
    runtime.children = {
        "hub": _Proc(pid=1),
        "access": _Proc(pid=2),
        "primary": _Proc(pid=3, returncode=7),
    }

    runtime._write_runtime_file(
        {"endpoint": "ws://127.0.0.1:1"},
        {"endpoint": "ws://127.0.0.1:2"},
    )

    payload = json.loads(runtime.config.runtime_file.read_text(encoding="utf-8"))
    assert payload["ready"] is True
    assert payload["access"]["pid"] == 2
    assert payload["children"]["hub"]["running"] is True
    assert payload["children"]["primary"]["running"] is False


def test_start_child_sets_unbuffered_env_and_tracks_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_popen(command: list[str], *, env: dict[str, str]) -> _Proc:
        calls.append({"command": command, "env": env})
        return _Proc(pid=42)

    runtime = SupervisorRuntime(
        SupervisorConfig(
            access_port=8331,
            state_dir=tmp_path / "state",
            workspace=tmp_path,
        )
    )
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    runtime._start_child(
        ChildSpec(name="hub", command=["python", "-m", "kernel.agent_hub"], runtime_file=tmp_path)
    )

    assert runtime.children["hub"].pid == 42
    assert calls[0]["command"] == ["python", "-m", "kernel.agent_hub"]
    assert calls[0]["env"]["PYTHONUNBUFFERED"] == "1"


def test_start_launches_children_in_order_and_writes_runtime_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = SupervisorRuntime(
        SupervisorConfig(
            access_port=8331,
            state_dir=tmp_path / "state",
            workspace=tmp_path,
        )
    )
    order: list[str] = []

    def fake_start_child(spec: ChildSpec) -> None:
        order.append(spec.name)
        runtime.children[spec.name] = _Proc(pid=len(order))

    def fake_wait_runtime_file(path: Path) -> dict[str, object]:
        return {"runtimeFile": path.name}

    monkeypatch.setattr(runtime, "_start_child", fake_start_child)
    monkeypatch.setattr(supervisor_runtime, "_wait_runtime_file", fake_wait_runtime_file)
    monkeypatch.setattr(supervisor_runtime, "_wait_http_readiness", lambda *_args: None)
    monkeypatch.setattr(supervisor_runtime, "_wait_default_route", lambda *_args: None)

    runtime.start()

    assert order == ["hub", "access", "primary"]
    payload = json.loads(runtime.config.runtime_file.read_text(encoding="utf-8"))
    assert payload["hub"] == {"runtimeFile": "agent-hub.json"}
    assert payload["primary"] == {"runtimeFile": "primary-agent.json"}


def test_start_removes_stale_runtime_files_before_waiting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = SupervisorRuntime(
        SupervisorConfig(
            access_port=8331,
            state_dir=tmp_path / "state",
            workspace=tmp_path,
        )
    )
    specs = runtime.build_specs()
    runtime.config.runtime_file.write_text('{"ready": true}', encoding="utf-8")
    for spec in specs.values():
        spec.runtime_file.write_text('{"stale": true}', encoding="utf-8")
    removed_before_start: dict[str, bool] = {}

    def fake_start_child(spec: ChildSpec) -> None:
        removed_before_start[spec.name] = not spec.runtime_file.exists()
        runtime.children[spec.name] = _Proc(pid=len(runtime.children) + 1)

    def fake_wait_runtime_file(path: Path) -> dict[str, object]:
        return {"runtimeFile": path.name}

    monkeypatch.setattr(runtime, "_start_child", fake_start_child)
    monkeypatch.setattr(supervisor_runtime, "_wait_runtime_file", fake_wait_runtime_file)
    monkeypatch.setattr(supervisor_runtime, "_wait_http_readiness", lambda *_args: None)
    monkeypatch.setattr(supervisor_runtime, "_wait_default_route", lambda *_args: None)

    runtime.start()

    assert removed_before_start == {"hub": True, "access": True, "primary": True}


def test_child_exit_restarts_from_failed_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = SupervisorRuntime(
        SupervisorConfig(
            access_port=8331,
            state_dir=tmp_path / "state",
            workspace=tmp_path,
        )
    )
    runtime.build_specs()
    runtime.children = {
        "hub": _Proc(pid=1),
        "access": _Proc(pid=2),
        "primary": _Proc(pid=3, returncode=9),
    }
    restarted: list[str] = []

    def fake_start_child(spec: ChildSpec) -> None:
        restarted.append(spec.name)
        runtime.children[spec.name] = _Proc(pid=10 + len(restarted))

    monkeypatch.setattr(runtime, "_start_child", fake_start_child)
    monkeypatch.setattr(
        supervisor_runtime, "_wait_runtime_file", lambda path: {"runtimeFile": path.name}
    )
    monkeypatch.setattr(supervisor_runtime, "_wait_default_route", lambda *_args: None)

    runtime._handle_child_exit("primary", 9)

    assert restarted == ["primary"]
    assert runtime.status == "ready"
    assert runtime.restart_counts["primary"] == 1
    assert runtime.last_exit is not None
    assert runtime.last_exit["child"] == "primary"
    assert runtime.last_exit["code"] == 9


def test_child_exit_enters_degraded_when_restart_budget_exceeded(tmp_path: Path) -> None:
    runtime = SupervisorRuntime(
        SupervisorConfig(
            access_port=8331,
            state_dir=tmp_path / "state",
            workspace=tmp_path,
        )
    )
    runtime.children = {
        "hub": _Proc(pid=1),
        "access": _Proc(pid=2),
        "primary": _Proc(pid=3, returncode=9),
    }
    runtime._restart_attempts = [time.time()] * supervisor_runtime.RESTART_BUDGET_MAX_ATTEMPTS

    runtime._handle_child_exit("primary", 9)

    payload = json.loads(runtime.config.runtime_file.read_text(encoding="utf-8"))
    assert runtime.status == "degraded"
    assert payload["status"] == "degraded"
    assert "restart budget exceeded" in payload["degradedReason"]


def test_stop_terminates_running_children_and_writes_stopped_file(tmp_path: Path) -> None:
    runtime = SupervisorRuntime(
        SupervisorConfig(
            access_port=8331,
            state_dir=tmp_path / "state",
            workspace=tmp_path,
        )
    )
    runtime.children = {
        "hub": _Proc(pid=1),
        "access": _Proc(pid=2),
        "primary": _Proc(pid=3),
    }

    runtime.stop()

    assert runtime.children["primary"].terminated is True
    assert runtime.children["access"].terminated is True
    assert runtime.children["hub"].terminated is True
    payload = json.loads(runtime.config.runtime_file.read_text(encoding="utf-8"))
    assert payload["ready"] is False
    assert payload["stopped"] is True


def test_stop_kills_child_that_does_not_exit_before_deadline(tmp_path: Path) -> None:
    runtime = SupervisorRuntime(
        SupervisorConfig(
            access_port=8331,
            state_dir=tmp_path / "state",
            workspace=tmp_path,
        )
    )
    stubborn = _Proc(pid=1, wait_raises_once=True)
    runtime.children = {"hub": stubborn}

    runtime.stop()

    assert stubborn.terminated is True
    assert stubborn.killed is True


def test_control_socket_routes_status_and_restart_agent(tmp_path: Path) -> None:
    target = _ControlTarget()
    server = SupervisorControlServer(
        SupervisorControlConfig(socket_path=tmp_path / "control.sock", token="secret"),
        target,
    )
    server.start()
    try:
        status = request_control(tmp_path / "control.sock", "secret", "status")
        restart = request_control(
            tmp_path / "control.sock",
            "secret",
            "restart_agent",
            {"agent_id": "primary", "reason": "test"},
        )
    finally:
        server.stop()

    assert status["status"] == "ready"
    assert restart["agent"] == "primary"
    assert target.restart_agent_calls == [("primary", "test")]


def test_control_socket_routes_over_tcp_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supervisor_control, "_supports_unix_socket", lambda: False)
    target = _ControlTarget()
    server = SupervisorControlServer(
        SupervisorControlConfig(socket_path=tmp_path / "control.sock", token="secret"),
        target,
    )
    server.start()
    try:
        marker = json.loads((tmp_path / "control.sock").read_text(encoding="utf-8"))
        status = request_control(tmp_path / "control.sock", "secret", "status")
    finally:
        server.stop()

    assert marker["transport"] == "tcp"
    assert marker["host"] == "127.0.0.1"
    assert status["status"] == "ready"


def test_wait_runtime_file_reads_json_when_written(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    path.write_text('{"ready": true}', encoding="utf-8")

    assert _wait_runtime_file(path, timeout=0.01) == {"ready": True}


def test_wait_runtime_file_times_out(tmp_path: Path) -> None:
    with pytest.raises(TimeoutError, match="runtime file not written"):
        _wait_runtime_file(tmp_path / "missing.json", timeout=0.01)


def test_write_json_replaces_file_atomically(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "runtime.json"

    _write_json(path, {"b": 2, "a": 1})

    assert path.read_text(encoding="utf-8") == '{"a": 1, "b": 2}'
    assert not path.with_suffix(".json.tmp").exists()


def test_wait_http_readiness_returns_when_process_and_hub_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(
            {
                "process_ready": True,
                "hub_ready": True,
            }
        ),
    )

    _wait_http_readiness("127.0.0.1", 8331, timeout=0.01)


def test_wait_http_readiness_times_out_when_readiness_never_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(
            {
                "process_ready": True,
                "hub_ready": False,
            }
        ),
    )

    with pytest.raises(TimeoutError, match="Access Agent did not become ready"):
        _wait_http_readiness("127.0.0.1", 8331, timeout=0.01)


def test_wait_default_route_returns_when_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(
            {
                "default_route_ready": True,
            }
        ),
    )

    _wait_default_route("127.0.0.1", 8331, timeout=0.01)


def test_wait_default_route_times_out_when_route_never_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(
            {
                "default_route_ready": False,
            }
        ),
    )

    with pytest.raises(TimeoutError, match="default_route_ready did not become true"):
        _wait_default_route("127.0.0.1", 8331, timeout=0.01)


def test_install_signal_handlers_stops_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = SupervisorRuntime(
        SupervisorConfig(
            access_port=8331,
            state_dir=tmp_path / "state",
            workspace=tmp_path,
        )
    )
    stopped = False
    handlers = {}

    def stop() -> None:
        nonlocal stopped
        stopped = True

    def fake_signal(signum: int, handler: object) -> None:
        handlers[signum] = handler

    monkeypatch.setattr(runtime, "stop", stop)
    monkeypatch.setattr("signal.signal", fake_signal)

    install_signal_handlers(runtime)
    with pytest.raises(SystemExit):
        handlers[15](15, None)

    assert stopped is True


def test_supervisor_core_does_not_import_fastapi() -> None:
    import kernel.supervisor.runtime as module

    assert "fastapi" not in module.__dict__


def test_child_kernel_spec_launches_nested_supervisor(tmp_path: Path) -> None:
    spec = build_child_kernel_spec(
        ChildKernelLaunch(
            agent_id="research",
            access_port=8444,
            state_dir=tmp_path / "child-state",
            workspace=tmp_path / "workspace",
            dev=True,
            prompt_backend="router",
        )
    )

    assert spec.name == "child-kernel:research"
    assert spec.command[2] == "kernel.supervisor"
    assert "--access-port" in spec.command
    assert "8444" in spec.command
    assert "--state-dir" in spec.command
    assert str(tmp_path / "child-state") in spec.command
    assert "--dev" in spec.command
    assert "--prompt-backend" in spec.command
    assert "router" in spec.command
    assert spec.runtime_file == tmp_path / "child-state" / "supervisor" / "supervisor.json"


class _Proc:
    def __init__(
        self,
        *,
        pid: int,
        returncode: int | None = None,
        wait_raises_once: bool = False,
    ) -> None:
        self.pid = pid
        self.returncode = returncode
        self.wait_raises_once = wait_raises_once
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.wait_raises_once:
            self.wait_raises_once = False
            raise subprocess.TimeoutExpired("child", timeout or 0)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _ControlTarget:
    def __init__(self) -> None:
        self.restart_agent_calls: list[tuple[str, str]] = []

    def control_status(self) -> dict[str, object]:
        return {"status": "ready"}

    def control_restart_runtime(self, reason: str) -> dict[str, object]:
        return {"status": "restarted", "reason": reason}

    def control_restart_agent(self, agent_id: str, reason: str) -> dict[str, object]:
        self.restart_agent_calls.append((agent_id, reason))
        return {"agent": agent_id, "reason": reason}

    def control_stop_runtime(self, reason: str) -> dict[str, object]:
        return {"status": "stopped", "reason": reason}
