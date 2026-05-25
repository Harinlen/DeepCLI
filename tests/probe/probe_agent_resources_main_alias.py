"""Probe main display alias and per-agent resource separation."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from kernel.agent_hub.manager.agent_network_service import AgentNetworkService
from kernel.agent_hub.manager.command_surface import AgentCommandService
from kernel.agent_hub.manager.manager import AgentManager
from kernel.agent_hub.manager.schemas import CreateAgentSpec


def main() -> None:
    with TemporaryDirectory() as tmp:
        home = Path(tmp)
        manager = AgentManager(home=home)
        manager.startup()
        try:
            commands = AgentCommandService(manager=manager)
            directory = AgentNetworkService(commands).list_visible_agents()
            assert directory["agents"][0]["agentId"] == "main"
            assert directory["agents"][0]["legacyAgentId"] == "primary"

            print("probe=agent_resources_main_alias")
            print("command=main_display result=PASS display=main legacy=primary")

            manager.create(
                CreateAgentSpec(
                    agent_id="worker",
                    name="Worker",
                    workspace=home / "worker-workspace",
                    state_dir=home / "agents" / "worker",
                ),
                actor_agent_id="primary",
            )
            primary = manager.get("primary")
            worker = manager.get("worker")
            assert primary is not None and worker is not None
            assert primary.state_dir != worker.state_dir
            print("command=resource_separation result=PASS shared_state_dir=false")
            workspace = Path(worker.workspace)
            assert (workspace / "AGENTS.md").exists()
            assert (workspace / "SOUL.md").exists()
            assert (workspace / "IDENTITY.md").exists()
            assert (Path(worker.state_dir) / "sessions").is_dir()
            print("command=workspace_bootstrap result=PASS files=AGENTS,SOUL,IDENTITY sessions_dir=true")

            try:
                manager.create(
                    CreateAgentSpec(
                        agent_id="bad",
                        name="Bad",
                        workspace=home / "bad-workspace",
                        state_dir=Path(worker.state_dir),
                    ),
                    actor_agent_id="primary",
                )
            except ValueError:
                print("command=reused_state_dir_rejected result=PASS")
            else:
                raise AssertionError("reused state_dir should fail")

            print("result=PASS")
        finally:
            manager.close()


if __name__ == "__main__":
    main()
