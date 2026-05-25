from __future__ import annotations

import json
from pathlib import Path

from kernel.agent_hub.manager.runtime_process import build_runtime_launch
from kernel.agent_hub.manager.schemas import AgentDefinitionRecord


def test_runtime_launch_passes_agent_identity_to_runtime(tmp_path: Path) -> None:
    definition = AgentDefinitionRecord(
        agent_id="research",
        name="Research",
        identity={"persona": "Investigates primary sources"},
        workspace=str(tmp_path / "workspace"),
        state_dir=str(tmp_path / "agents" / "research"),
        updated_at="2026-05-25T00:00:00+00:00",
        updated_by_agent_id="primary",
    )

    launch = build_runtime_launch(
        definition,
        router_endpoint="ws://127.0.0.1:8123",
        router_token="token",
    )
    command = list(launch.command)

    assert command[command.index("--agent-name") + 1] == "Research"
    raw_identity = command[command.index("--agent-identity-json") + 1]
    assert json.loads(raw_identity) == {"persona": "Investigates primary sources"}
