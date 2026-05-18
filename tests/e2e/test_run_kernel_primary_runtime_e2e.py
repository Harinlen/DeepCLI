from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.request
import asyncio
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from probe.client import AgentChunk, ProbeClient, TurnComplete

pytestmark = pytest.mark.e2e


def test_run_kernel_autostarts_primary_runtime_and_routes_prompt(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    port = _free_port()
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    fake_llm = _FakeOpenAIServer()
    fake_llm.start()
    _write_llm_config(home, fake_llm.base_url)
    proc = _start_run_kernel(repo, home, workspace, port)
    try:
        _wait_health(port, proc)
        registered = _wait_registered(port, proc)
        assert "primary" in registered

        token = (home / "state" / "auth_token").read_text(encoding="utf-8").strip()
        response = asyncio.run(_send_prompt(port, token, workspace))

        assert response["id"] == "run-kernel-turn"
        assert "result" in response
        assert response["result"]["text"]
    finally:
        _terminate_process_group(proc)
        fake_llm.stop()


def test_run_cli_uses_run_kernel_primary_runtime(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    port = _free_port()
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    fake_llm = _FakeOpenAIServer()
    fake_llm.start()
    _write_llm_config(home, fake_llm.base_url)
    proc = _start_run_kernel(repo, home, workspace, port)
    try:
        _wait_health(port, proc)
        registered = _wait_registered(port, proc)
        assert "primary" in registered
        _wait_default_route_ready(port, proc)

        result = subprocess.run(  # nosec B603
            ["bash", "scripts/run-cli.sh", "--print", "ping"],
            cwd=repo,
            env={
                **os.environ,
                "KERNEL_PORT": str(port),
                "DEEPCLI_HOME": str(home),
                "DEEPCLI_STATE_DIR": str(home / "state"),
                "DEEPCLI_CONFIG_DIR": str(home / "config"),
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )

        assert result.returncode == 0, result.stdout
        assert "RUN_KERNEL_MODEL_OK" in result.stdout
    finally:
        _terminate_process_group(proc)
        fake_llm.stop()


def test_run_kernel_ctrl_c_shutdown_has_no_runtime_traceback(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    port = _free_port()
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    proc = _start_run_kernel(repo, home, workspace, port)
    try:
        _wait_health(port, proc)
        registered = _wait_registered(port, proc)
        assert "primary" in registered

        _interrupt_process_group(proc)

        log = (home / "kernel.log").read_text(encoding="utf-8", errors="replace")
        assert "Traceback (most recent call last)" not in log
        assert "asyncio.exceptions.CancelledError" not in log
        assert "KeyboardInterrupt" not in log
    finally:
        _terminate_process_group(proc)


def _start_run_kernel(
    repo: Path,
    home: Path,
    workspace: Path,
    port: int,
) -> subprocess.Popen[str]:
    log = (home / "kernel.log").open("w", encoding="utf-8")
    return subprocess.Popen(  # nosec B603
        [
            "bash",
            "scripts/run-kernel.sh",
            "--access-port",
            str(port),
            "--state-dir",
            str(home / "state"),
            "--workspace",
            str(workspace),
            "--dev",
        ],
        cwd=repo,
        env={
            **os.environ,
            "DEEPCLI_HOME": str(home),
            "DEEPCLI_STATE_DIR": str(home / "state"),
            "DEEPCLI_CONFIG_DIR": str(home / "config"),
        },
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_health(port: int, proc: subprocess.Popen[str]) -> None:
    deadline = time.time() + 45
    while time.time() < deadline:
        _raise_if_exited(proc)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/access/readiness", timeout=1) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("process_ready") is True:
                return
            time.sleep(0.2)
        except Exception:
            time.sleep(0.2)
    raise AssertionError(_failure_output(proc, "kernel did not become healthy"))


def _wait_registered(port: int, proc: subprocess.Popen[str]) -> set[str]:
    deadline = time.time() + 45
    while time.time() < deadline:
        _raise_if_exited(proc)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/access/readiness", timeout=1) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("primary_registered") is True:
                return {"primary"}
            time.sleep(0.2)
        except Exception:
            time.sleep(0.2)
    raise AssertionError(_failure_output(proc, "primary runtime did not register"))


def _wait_default_route_ready(port: int, proc: subprocess.Popen[str]) -> None:
    deadline = time.time() + 45
    while time.time() < deadline:
        _raise_if_exited(proc)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/access/readiness", timeout=1) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("default_route_ready") is True:
                return
            time.sleep(0.2)
        except Exception:
            time.sleep(0.2)
    raise AssertionError(_failure_output(proc, "default route did not become ready"))


async def _send_prompt(port: int, token: str, workspace: Path) -> dict[str, Any]:
    text_parts: list[str] = []
    stop_reason = "unknown"
    async with ProbeClient(port=port, token=token, request_timeout=15) as client:
        await client.initialize()
        session_id = await client.new_session(cwd=str(workspace))
        async for event in client.prompt(
            session_id,
            "ping",
            client_turn_id=str(uuid.uuid4()),
            timeout=15,
        ):
            if isinstance(event, AgentChunk):
                text_parts.append(event.text)
            elif isinstance(event, TurnComplete):
                stop_reason = event.stop_reason
    return {
        "id": "run-kernel-turn",
        "result": {"text": "".join(text_parts), "stopReason": stop_reason},
    }


def _raise_if_exited(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        raise AssertionError(_failure_output(proc, f"kernel exited with {proc.returncode}"))


def _failure_output(proc: subprocess.Popen[str], message: str) -> str:
    return f"{message}; pid={proc.pid}; returncode={proc.poll()}"


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=3)


def _interrupt_process_group(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    os.killpg(proc.pid, signal.SIGINT)
    try:
        proc.wait(timeout=12)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)


class _FakeOpenAIServer:
    def __init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("fake server is not running")
        address = self._server.server_address
        return f"http://{str(address[0])}:{address[1]}/v1"

    def start(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.endswith("/models"):
            self._json({"data": [{"id": "run-kernel-model"}]})
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.endswith("/chat/completions"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for payload in (
            {"choices": [{"delta": {"content": "RUN_KERNEL_MODEL_OK"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ):
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _json(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _write_llm_config(home: Path, fake_base_url: str) -> None:
    path = home / "config" / "kernel.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "llm:",
                "  providers:",
                "    run_kernel_fake:",
                "      type: openai_compatible",
                f"      base_url: {fake_base_url}",
                "      api_key: run-kernel-test",
                "      models:",
                "        - run-kernel-model",
                "  current_used:",
                "    default:",
                "      - run_kernel_fake",
                "      - run-kernel-model",
                "    compact:",
                "      - run_kernel_fake",
                "      - run-kernel-model",
                "",
            ]
        ),
        encoding="utf-8",
    )
