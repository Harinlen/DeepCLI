from __future__ import annotations

from pathlib import Path

import pytest

from kernel.supervisor import SupervisorConfig, SupervisorRuntime

pytestmark = pytest.mark.e2e


def test_supervisor_top_level_children_are_hub_and_access_router(tmp_path: Path) -> None:
    runtime = SupervisorRuntime(
        SupervisorConfig(
            access_port=8331,
            state_dir=tmp_path / "state",
            workspace=tmp_path / "workspace",
        )
    )

    specs = runtime.build_specs()

    assert tuple(specs) == ("hub", "access_router")
    assert specs["hub"].command[2] == "kernel.agent_hub"
    assert specs["access_router"].command[2] == "kernel.access_router"
    assert "kernel.agents.mustang.runtime" not in " ".join(
        part for spec in specs.values() for part in spec.command
    )
