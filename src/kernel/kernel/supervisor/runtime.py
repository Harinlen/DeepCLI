"""Supervisor process launcher for Agent Control Plane C1."""

from __future__ import annotations

import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from kernel.paths import user_path
from kernel.supervisor.control import SupervisorControlConfig, SupervisorControlServer

RESTART_BUDGET_WINDOW_SECONDS = 60
RESTART_BUDGET_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class SupervisorConfig:
    """Inputs for one Supervisor launch."""

    access_port: int
    state_dir: Path
    workspace: Path
    host: str = "127.0.0.1"
    dev: bool = False
    prompt_backend: str = "router"

    @property
    def runtime_dir(self) -> Path:
        return self.state_dir / "supervisor"

    @property
    def runtime_file(self) -> Path:
        return self.runtime_dir / "supervisor.json"

    @property
    def control_socket(self) -> Path:
        return self.runtime_dir / "control.sock"


@dataclass(frozen=True)
class ChildSpec:
    """One child process command and runtime file."""

    name: str
    command: list[str]
    runtime_file: Path


class SupervisorRuntime:
    """Launch and monitor Agent Hub, Access Agent, and Primary Agent."""

    def __init__(self, config: SupervisorConfig) -> None:
        self.config = config
        self.primary_token = secrets.token_urlsafe(32)
        self.control_token = secrets.token_urlsafe(32)
        self.children: dict[str, subprocess.Popen[bytes]] = {}
        self.specs: dict[str, ChildSpec] = {}
        self.restart_counts: dict[str, int] = {}
        self._restart_attempts: list[float] = []
        self.last_exit: dict[str, object] | None = None
        self.degraded_reason: str | None = None
        self.status = "starting"
        self._control: SupervisorControlServer | None = None
        self._restart_lock = threading.Lock()

    def build_specs(self) -> dict[str, ChildSpec]:
        """Build child process commands with allocated loopback ports."""

        self.config.runtime_dir.mkdir(parents=True, exist_ok=True)
        hub_port = _free_port(self.config.host)
        primary_port = _free_port(self.config.host)
        hub_file = self.config.runtime_dir / "agent-hub.json"
        access_file = self.config.runtime_dir / "access-agent.json"
        primary_file = self.config.runtime_dir / "primary-agent.json"
        hub_endpoint = f"ws://{self.config.host}:{hub_port}"
        state_dir = user_path("agents", "primary")
        session_store = state_dir / "sessions" / "sessions.db"

        python = sys.executable
        specs = {
            "hub": ChildSpec(
                name="hub",
                runtime_file=hub_file,
                command=[
                    python,
                    "-m",
                    "kernel.agent_hub",
                    "--host",
                    self.config.host,
                    "--port",
                    str(hub_port),
                    "--runtime-file",
                    str(hub_file),
                    f"--primary-token={self.primary_token}",
                    "--workspace",
                    str(self.config.workspace),
                ],
            ),
            "access": ChildSpec(
                name="access",
                runtime_file=access_file,
                command=[
                    python,
                    "-m",
                    "kernel.access_agent",
                    "--host",
                    self.config.host,
                    "--port",
                    str(self.config.access_port),
                    "--hub-endpoint",
                    hub_endpoint,
                    "--prompt-backend",
                    self.config.prompt_backend,
                    "--supervisor-control-socket",
                    str(self.config.control_socket),
                    "--supervisor-control-token",
                    self.control_token,
                    *(["--dev"] if self.config.dev else []),
                ],
            ),
            "primary": ChildSpec(
                name="primary",
                runtime_file=primary_file,
                command=[
                    python,
                    "-m",
                    "kernel.agent_runtime",
                    "--agent-id",
                    "primary",
                    "--host",
                    self.config.host,
                    "--port",
                    str(primary_port),
                    "--hub-endpoint",
                    hub_endpoint,
                    f"--registration-token={self.primary_token}",
                    "--state-dir",
                    str(state_dir),
                    "--session-store-path",
                    str(session_store),
                    "--workspace",
                    str(self.config.workspace),
                    "--runtime-file",
                    str(primary_file),
                    "--supervisor-control-socket",
                    str(self.config.control_socket),
                    "--supervisor-control-token",
                    self.control_token,
                ],
            ),
        }
        self.specs = specs
        return specs

    def start(self) -> None:
        """Start children in the C1-required order and write runtime state."""

        specs = self.build_specs()
        for spec in specs.values():
            spec.runtime_file.unlink(missing_ok=True)
        self.config.runtime_file.unlink(missing_ok=True)
        self._start_child(specs["hub"])
        hub_state = _wait_runtime_file(specs["hub"].runtime_file)
        self._start_child(specs["access"])
        _wait_http_readiness(self.config.host, self.config.access_port)
        self._start_child(specs["primary"])
        primary_state = _wait_runtime_file(specs["primary"].runtime_file)
        _wait_default_route(self.config.host, self.config.access_port)
        self.status = "ready"
        self._start_control_server()
        self._write_runtime_file(hub_state, primary_state)

    def wait(self) -> None:
        """Monitor children and restart crashed processes."""

        while True:
            for name, proc in self.children.items():
                code = proc.poll()
                if code is not None:
                    if self._restart_lock.locked():
                        continue
                    self._handle_child_exit(name, code)
                    break
            time.sleep(0.25)

    def stop(self) -> None:
        """Stop all children in reverse startup order."""

        for name in ("primary", "access", "hub"):
            proc = self.children.get(name)
            if proc is None or proc.poll() is not None:
                continue
            proc.terminate()
        deadline = time.time() + 8
        for proc in self.children.values():
            remaining = max(0.1, deadline - time.time())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        self._write_stopped_runtime_file()
        if self._control is not None:
            self._control.stop()
            self._control = None

    def _start_child(self, spec: ChildSpec) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["MUSTANG_AGENT_ID"] = "primary" if spec.name == "primary" else spec.name
        env["MUSTANG_SUPERVISOR_CONTROL_SOCKET"] = str(self.config.control_socket)
        env["MUSTANG_SUPERVISOR_CONTROL_TOKEN"] = self.control_token
        env["MUSTANG_SUPERVISOR_RUNTIME_FILE"] = str(self.config.runtime_file)
        proc = subprocess.Popen(spec.command, env=env)
        self.children[spec.name] = proc

    def _handle_child_exit(self, name: str, code: int) -> None:
        self.last_exit = {"child": name, "code": code, "at": _now()}
        if not self._consume_restart_budget():
            self.status = "degraded"
            self.degraded_reason = (
                f"restart budget exceeded after {RESTART_BUDGET_MAX_ATTEMPTS} attempts "
                f"in {RESTART_BUDGET_WINDOW_SECONDS}s"
            )
            self._write_runtime_file({}, {})
            return
        self.status = "restarting"
        self.degraded_reason = None
        self.restart_counts[name] = self.restart_counts.get(name, 0) + 1
        self._write_runtime_file({}, {})
        self._restart_from(name)

    def _restart_from(self, name: str) -> None:
        with self._restart_lock:
            self._restart_from_locked(name)

    def _restart_from_locked(self, name: str) -> None:
        order = ["hub", "access", "primary"]
        if name not in order:
            return
        start_index = order.index(name)
        for child_name in reversed(order[start_index:]):
            proc = self.children.get(child_name)
            if proc is not None and proc.poll() is None:
                proc.terminate()
        deadline = time.time() + 8
        for child_name in order[start_index:]:
            proc = self.children.get(child_name)
            if proc is None or proc.poll() is not None:
                continue
            try:
                proc.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

        specs = self.specs or self.build_specs()
        for child_name in order[start_index:]:
            specs[child_name].runtime_file.unlink(missing_ok=True)

        if start_index <= 0:
            self._start_child(specs["hub"])
            hub_state = _wait_runtime_file(specs["hub"].runtime_file)
        else:
            hub_state = _read_json(specs["hub"].runtime_file)
        if start_index <= 1:
            self._start_child(specs["access"])
            _wait_http_readiness(self.config.host, self.config.access_port)
        if start_index <= 2:
            self._start_child(specs["primary"])
        primary_state = _wait_runtime_file(specs["primary"].runtime_file)
        _wait_default_route(self.config.host, self.config.access_port)
        self.status = "ready"
        self.degraded_reason = None
        self._write_runtime_file(hub_state, primary_state)

    def _start_control_server(self) -> None:
        if self._control is not None:
            return
        self._control = SupervisorControlServer(
            SupervisorControlConfig(
                socket_path=self.config.control_socket,
                token=self.control_token,
            ),
            self,
        )
        self._control.start()

    def control_status(self) -> dict[str, object]:
        hub_spec = self.specs.get("hub")
        primary_spec = self.specs.get("primary")
        self._write_runtime_file(
            _read_json(hub_spec.runtime_file) if hub_spec is not None else {},
            _read_json(primary_spec.runtime_file) if primary_spec is not None else {},
        )
        return _read_json(self.config.runtime_file)

    def control_restart_runtime(self, reason: str) -> dict[str, object]:
        self.last_exit = {"child": "runtime", "code": "requested", "reason": reason, "at": _now()}
        if not self._consume_restart_budget():
            self.status = "degraded"
            self.degraded_reason = "restart budget exceeded during requested runtime restart"
            self._write_runtime_file({}, {})
            return self.control_status()
        self.status = "restarting"
        self.degraded_reason = None
        threading.Thread(target=self._restart_from, args=("hub",), daemon=True).start()
        self._write_runtime_file({}, {})
        return self.control_status()

    def control_restart_agent(self, agent_id: str, reason: str) -> dict[str, object]:
        if agent_id != "primary":
            raise ValueError(f"unknown supervised agent: {agent_id!r}")
        self.last_exit = {"child": "primary", "code": "requested", "reason": reason, "at": _now()}
        if not self._consume_restart_budget():
            self.status = "degraded"
            self.degraded_reason = "restart budget exceeded during requested agent restart"
            self._write_runtime_file({}, {})
            return self.control_status()
        self.status = "restarting"
        self.degraded_reason = None
        threading.Thread(target=self._restart_from, args=("primary",), daemon=True).start()
        self._write_runtime_file({}, {})
        return self.control_status()

    def control_stop_runtime(self, reason: str) -> dict[str, object]:
        self.last_exit = {
            "child": "runtime",
            "code": "stop_requested",
            "reason": reason,
            "at": _now(),
        }
        self.stop()
        return {"status": "stopped"}

    def _write_runtime_file(
        self,
        hub_state: dict[str, object],
        primary_state: dict[str, object],
    ) -> None:
        payload = {
            "ready": True,
            "status": self.status,
            "degradedReason": self.degraded_reason,
            "supervisor": {"pid": os.getpid()},
            "control": {"socket": str(self.config.control_socket)},
            "access": {
                "pid": self.children["access"].pid if "access" in self.children else None,
                "endpoint": f"http://{self.config.host}:{self.config.access_port}",
            },
            "hub": hub_state,
            "primary": primary_state,
            "restartCounts": self.restart_counts,
            "lastExit": self.last_exit,
            "children": {
                name: {"pid": proc.pid, "running": proc.poll() is None}
                for name, proc in self.children.items()
            },
        }
        _write_json(self.config.runtime_file, payload)

    def _write_stopped_runtime_file(self) -> None:
        payload = {
            "ready": False,
            "status": "stopped",
            "stopped": True,
            "children": {
                name: {"pid": proc.pid, "returncode": proc.poll()}
                for name, proc in self.children.items()
            },
        }
        _write_json(self.config.runtime_file, payload)

    def _consume_restart_budget(self) -> bool:
        now = time.time()
        earliest = now - RESTART_BUDGET_WINDOW_SECONDS
        self._restart_attempts = [
            attempt for attempt in self._restart_attempts if attempt >= earliest
        ]
        if len(self._restart_attempts) >= RESTART_BUDGET_MAX_ATTEMPTS:
            return False
        self._restart_attempts.append(now)
        return True


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _wait_runtime_file(path: Path, timeout: float = 15) -> dict[str, object]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.05)
    raise TimeoutError(f"runtime file not written: {path}")


def _read_json(path: Path) -> dict[str, object]:
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
    return {}


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _wait_http_readiness(host: str, port: int, timeout: float = 45) -> None:
    import urllib.request

    deadline = time.time() + timeout
    url = f"http://{host}:{port}/access/readiness"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("process_ready") and payload.get("hub_ready"):
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError("Access Agent did not become ready")


def _wait_default_route(host: str, port: int, timeout: float = 20) -> None:
    import urllib.request

    deadline = time.time() + timeout
    url = f"http://{host}:{port}/access/readiness"
    while time.time() < deadline:
        with urllib.request.urlopen(url, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("default_route_ready"):
            return
        time.sleep(0.2)
    raise TimeoutError("default_route_ready did not become true")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def install_signal_handlers(runtime: SupervisorRuntime) -> None:
    """Install SIGTERM/SIGINT cleanup handlers for CLI use."""

    def _handler(_signum: int, _frame: object) -> None:
        runtime.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
