from __future__ import annotations

from kernel.agent_hub.manager.spawned_runs import SpawnedRunRegistry


def test_spawned_run_registry_lifecycle(tmp_path):
    registry = SpawnedRunRegistry.open(tmp_path)
    try:
        run = registry.spawn(
            parent_session_id="parent",
            requester_agent_id="primary",
            target_agent_id="research",
            runtime="agent",
            mode="session",
            task="do work",
        )

        assert run.status == "running"
        assert run.session_id.startswith("agent:research:subagent:")
        assert run.provenance["kind"] == "inter_session"
        assert registry.get_owned(run.run_id, requester_agent_id="primary") is not None
        assert registry.get_owned(run.run_id, requester_agent_id="other") is None

        steered = registry.steer(run.run_id, requester_agent_id="primary", message="continue")
        assert steered.last_message == "continue"
        assert steered.revision == 2

        completed = registry.complete(
            run.run_id,
            requester_agent_id="primary",
            result={"text": "done"},
        )
        assert completed.status == "completed"
        assert completed.result == {"text": "done"}

        stopped = registry.stop(run.run_id, requester_agent_id="primary")
        assert stopped.status == "stopped"
        assert stopped.revision == 4
        assert [event["eventType"] for event in registry.events(run.run_id, requester_agent_id="primary")] == [
            "spawned",
            "message",
            "completed",
            "stopped",
        ]
    finally:
        registry.close()
