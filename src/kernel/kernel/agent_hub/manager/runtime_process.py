"""AgentManager-owned Agent Runtime process launcher."""

from __future__ import annotations

import os
import orjson
import socket
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from pathlib import Path

from kernel.agent_hub.manager.schemas import AgentDefinitionRecord


@dataclass(frozen=True, slots=True)
class AgentRuntimeLaunch:
    """Concrete command for one durable Agent Runtime process."""

    agent_id: str
    command: tuple[str, ...]
    runtime_file: Path
    deepcli_home: Path


def build_runtime_launch(
    definition: AgentDefinitionRecord,
    *,
    router_endpoint: str,
    router_token: str,
) -> AgentRuntimeLaunch:
    """Build the AgentManager-owned runtime command from durable definition."""
    runtime_file = Path(definition.state_dir) / "runtime.json"
    host = "127.0.0.1"
    port = _free_port(host)
    command = tuple(definition.runtime.command) or (
        sys.executable,
        "-m",
        "kernel.agents.mustang.runtime",
        "--agent-id",
        definition.agent_id,
        "--agent-name",
        definition.name,
        "--agent-identity-json",
        orjson.dumps(definition.identity).decode("utf-8"),
        "--host",
        host,
        "--port",
        str(port),
        "--access-router-endpoint",
        router_endpoint,
        f"--registration-token={router_token}",
        "--state-dir",
        definition.state_dir,
        "--session-store-path",
        str(Path(definition.state_dir) / "sessions" / "sessions.db"),
        "--workspace",
        definition.workspace,
        "--resource-home",
        str(Path(definition.state_dir).parent.parent),
        "--runtime-file",
        str(runtime_file),
    )
    return AgentRuntimeLaunch(
        agent_id=definition.agent_id,
        command=command,
        runtime_file=runtime_file,
        deepcli_home=Path(definition.state_dir).parent.parent,
    )


def spawn_runtime(launch: AgentRuntimeLaunch) -> subprocess.Popen[bytes]:
    """Spawn one runtime process from a fixed AgentManager launch command."""
    launch.runtime_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    launch.runtime_file.unlink(missing_ok=True)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["MUSTANG_AGENT_ID"] = launch.agent_id
    env["DEEPCLI_HOME"] = str(launch.deepcli_home)
    env["DEEPCLI_STATE_DIR"] = str(launch.deepcli_home / "state")
    env["DEEPCLI_CONFIG_DIR"] = str(launch.deepcli_home / "config")
    return subprocess.Popen(list(launch.command), env=env)  # nosec B603


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
