"""Final monolithic Global ResourceStore migration closure probe.

This probe is intentionally an orchestrator: the individual subsystem probes
own the detailed real-subsystem assertions, while this file gives the migration
plan one stable acceptance command that proves the full evidence set still
passes together.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
KERNEL_PROJECT = ROOT / "src" / "kernel"
CLI_PROJECT = ROOT / "src" / "cli"


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    """One subprocess-backed closure check in the final migration suite."""

    name: str
    command: tuple[str, ...]
    cwd: Path
    required_markers: tuple[str, ...]


def main() -> None:
    """Run the final ResourceStore migration acceptance matrix."""
    targets = (
        _python_probe(
            "aggregate_global_resource_closure",
            "tests/probe/probe_global_resource_store_closure.py",
            (
                "config_refresh=True",
                "flags_frozen_snapshot=True",
                "secret_ref_stable=True",
                "agents_gateways_share_access_channel_bindings=True",
                "agent_bindings=0",
                "schedule_startup_from_resource_store=True",
                "result=PASS",
            ),
        ),
        _python_probe(
            "mcp_resource_store_management",
            "tests/probe/probe_mcp_resource_store.py",
            (
                "mcp_management_surface=True",
                "legacy_drift_ignored=True",
                "mcp_plaintext_leaked=False",
                "management_response_plaintext_leaked=False",
                "result=PASS",
            ),
        ),
        _python_probe(
            "tool_policy_resource_store",
            "tests/probe/probe_tool_policy_resource_store.py",
            (
                "tool_policy_startup_from_resource_store=True",
                "policy_refresh_updates_before_call=True",
                "tool_config_plaintext_leaked=False",
                "result=PASS",
            ),
        ),
        _python_probe(
            "skill_resource_store",
            "tests/probe/probe_skill_resource_store.py",
            (
                "skill_startup_from_resource_store=True",
                "legacy_drift_ignored=True",
                "skill_body_plaintext_leaked=False",
                "result=PASS",
            ),
        ),
        _python_probe(
            "hook_resource_store",
            "tests/probe/probe_hook_resource_store.py",
            (
                "hook_startup_from_resource_store=True",
                "runtime_execution_state_persisted=False",
                "handler_plaintext_leaked=False",
                "result=PASS",
            ),
        ),
        _python_probe(
            "memory_resource_store",
            "tests/probe/probe_memory_resource_store.py",
            (
                "memory_startup_from_resource_store=True",
                "memory_data_persisted_as_declaration=False",
                "plaintext_secret_leaked=False",
                "result=PASS",
            ),
        ),
        _python_probe(
            "prompt_resource_store",
            "tests/probe/probe_prompt_resource_store.py",
            (
                "prompt_startup_from_resource_store=True",
                "prompt_body_persisted_as_declaration=False",
                "rendered_prompt_persisted=False",
                "result=PASS",
            ),
        ),
        _python_probe(
            "schedule_resource_store",
            "tests/probe/probe_schedule_resource_store.py",
            (
                "schedule_manager_startup_from_resource_store=True",
                "agent_delete_disabled_schedule=True",
                "result=PASS",
            ),
        ),
        ProbeTarget(
            name="cli_slash_management_dispatch",
            command=("bun", "run", "tests/probe_global_resource_slash_commands.ts"),
            cwd=CLI_PROJECT,
            required_markers=(
                "mcp_management_via_acp=true",
                "gateway_create_delete_via_acp=true",
                "sqlite_direct_writes=0",
                "result=PASS",
            ),
        ),
        ProbeTarget(
            name="real_kernel_slash_commands",
            command=("bun", "run", "tests/probe_real_kernel_slash_commands.ts"),
            cwd=CLI_PROJECT,
            required_markers=(
                "kernel_status_via_real_acp=true",
                "skills_management_via_real_acp=true",
                "result=PASS",
            ),
        ),
    )

    print("probe=global_resource_store_final_e2e")
    for target in targets:
        output = _run(target)
        print(f"{target.name}=PASS")
        for marker in target.required_markers:
            if marker.startswith("result="):
                continue
            print(f"{target.name}.{marker}")
        _assert_markers(target, output)
    print("agent_bindings=reserved_deferred")
    print("full_monolithic_e2e=True")
    print("result=PASS")


def _python_probe(
    name: str,
    script: str,
    required_markers: tuple[str, ...],
) -> ProbeTarget:
    return ProbeTarget(
        name=name,
        command=(sys.executable, script),
        cwd=ROOT,
        required_markers=required_markers,
    )


def _run(target: ProbeTarget) -> str:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    completed = subprocess.run(
        target.command,
        cwd=target.cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )
    output = completed.stdout
    if completed.returncode != 0:
        raise AssertionError(f"{target.name} failed with exit={completed.returncode}\n{output}")
    return output


def _assert_markers(target: ProbeTarget, output: str) -> None:
    missing = [marker for marker in target.required_markers if marker not in output]
    if missing:
        raise AssertionError(f"{target.name} missing markers {missing!r}\n--- output ---\n{output}")


if __name__ == "__main__":
    main()
