"""Local Supervisor control socket.

The control plane is intentionally private to the current OS user.  It
exposes runtime lifecycle operations to the launcher and Access Agent without
putting process management into model-visible shell commands.
"""

from __future__ import annotations

import json
import os
import socket
import socketserver
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast


class SupervisorControlTarget(Protocol):
    """Runtime methods exposed through the local control socket."""

    def control_status(self) -> dict[str, object]: ...

    def control_restart_runtime(self, reason: str) -> dict[str, object]: ...

    def control_restart_agent(self, agent_id: str, reason: str) -> dict[str, object]: ...

    def control_stop_runtime(self, reason: str) -> dict[str, object]: ...


@dataclass(frozen=True)
class SupervisorControlConfig:
    socket_path: Path
    token: str


class SupervisorControlServer:
    """Small JSON-over-local-socket server for Supervisor lifecycle control."""

    def __init__(self, config: SupervisorControlConfig, target: SupervisorControlTarget) -> None:
        self.config = config
        self._target = target
        self._server: socketserver.BaseServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.config.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.socket_path.unlink(missing_ok=True)

        target = self._target
        token = self.config.token

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                raw = self.rfile.readline()
                try:
                    request = json.loads(raw.decode("utf-8"))
                    if request.get("token") != token:
                        raise PermissionError("invalid supervisor control token")
                    response = _dispatch(target, request)
                except Exception as exc:
                    response = {
                        "ok": False,
                        "error": type(exc).__name__,
                        "message": str(exc),
                    }
                self.wfile.write(json.dumps(response, sort_keys=True).encode("utf-8") + b"\n")

        if _supports_unix_socket():
            unix_server = getattr(socketserver, "UnixStreamServer")
            self._server = unix_server(str(self.config.socket_path), Handler)
            self.config.socket_path.chmod(0o600)
        else:
            self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
            host, port = cast(tuple[str, int], self._server.server_address)
            self.config.socket_path.write_text(
                json.dumps({"transport": "tcp", "host": host, "port": port}, sort_keys=True),
                encoding="utf-8",
            )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="supervisor-control",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self.config.socket_path.unlink(missing_ok=True)


def _dispatch(target: SupervisorControlTarget, request: dict[str, Any]) -> dict[str, object]:
    method = str(request.get("method", ""))
    raw_params = request.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    if method == "status":
        return {"ok": True, **target.control_status()}
    if method == "restart_runtime":
        return {
            "ok": True,
            **target.control_restart_runtime(str(params.get("reason") or "control restart")),
        }
    if method == "restart_agent":
        return {
            "ok": True,
            **target.control_restart_agent(
                str(params.get("agent_id") or params.get("agentId") or ""),
                str(params.get("reason") or "agent self restart"),
            ),
        }
    if method == "stop_runtime":
        return {
            "ok": True,
            **target.control_stop_runtime(str(params.get("reason") or "control stop")),
        }
    raise ValueError(f"unknown supervisor control method: {method}")


def request_control(
    socket_path: str | os.PathLike[str],
    token: str,
    method: str,
    params: dict[str, object] | None = None,
    *,
    timeout: float = 10.0,
) -> dict[str, object]:
    """Send one request to the local Supervisor control socket."""
    payload = {
        "token": token,
        "method": method,
        "params": params or {},
    }
    endpoint = _resolve_control_endpoint(socket_path)
    if endpoint["transport"] == "unix":
        sock = socket.socket(cast(int, getattr(socket, "AF_UNIX")), socket.SOCK_STREAM)
        connect_target: str | tuple[str, int] = str(socket_path)
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connect_target = (str(endpoint["host"]), int(endpoint["port"]))

    with sock:
        sock.settimeout(timeout)
        sock.connect(connect_target)
        sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    raw = b"".join(chunks).split(b"\n", 1)[0]
    response = json.loads(raw.decode("utf-8"))
    if response.get("ok") is not True:
        message = response.get("message") or response.get("error") or "supervisor control failed"
        raise RuntimeError(str(message))
    return response


def _supports_unix_socket() -> bool:
    return (
        sys.platform != "win32"
        and hasattr(socket, "AF_UNIX")
        and hasattr(socketserver, "UnixStreamServer")
    )


def _resolve_control_endpoint(
    socket_path: str | os.PathLike[str],
) -> dict[str, str | int]:
    if _supports_unix_socket():
        return {"transport": "unix"}
    marker_path = Path(socket_path)
    payload = json.loads(marker_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("transport") != "tcp":
        raise RuntimeError(f"invalid supervisor control endpoint marker: {marker_path}")
    return {
        "transport": "tcp",
        "host": str(payload.get("host") or "127.0.0.1"),
        "port": int(payload["port"]),
    }
