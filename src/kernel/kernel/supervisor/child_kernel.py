"""Child Mustang kernel launch contracts.

Child kernels are durable peer backends managed by Supervisor.  They are not
AgentTool children and do not enter the parent Agent's private task registry.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from kernel.supervisor.runtime import ChildSpec


@dataclass(frozen=True)
class ChildKernelLaunch:
    """Inputs needed to spawn one child Mustang kernel supervisor."""

    agent_id: str
    access_port: int
    state_dir: Path
    workspace: Path
    host: str = "127.0.0.1"
    dev: bool = False
    prompt_backend: str = "router"

    @property
    def runtime_file(self) -> Path:
        """Runtime file written by the child supervisor."""

        return self.state_dir / "supervisor" / "supervisor.json"


def build_child_kernel_spec(config: ChildKernelLaunch) -> ChildSpec:
    """Build a Supervisor child spec for a peer child kernel."""

    command = [
        sys.executable,
        "-m",
        "kernel.supervisor",
        "--host",
        config.host,
        "--access-port",
        str(config.access_port),
        "--state-dir",
        str(config.state_dir),
        "--workspace",
        str(config.workspace),
        "--prompt-backend",
        config.prompt_backend,
    ]
    if config.dev:
        command.append("--dev")
    return ChildSpec(
        name=f"child-kernel:{config.agent_id}",
        command=command,
        runtime_file=config.runtime_file,
    )


__all__ = ["ChildKernelLaunch", "build_child_kernel_spec"]
