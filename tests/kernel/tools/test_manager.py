"""ToolManager subsystem — startup + snapshot integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from kernel.config import ConfigManager
from kernel.flags import FlagManager
from kernel.module_table import KernelModuleTable
from kernel.prompts.manager import PromptManager
from kernel.tools import ToolManager


@pytest.fixture
async def module_table(tmp_path: Path) -> KernelModuleTable:
    """Minimal module table rooted in ``tmp_path``."""
    flags = FlagManager(path=tmp_path / "flags.yaml")
    await flags.initialize()

    config = ConfigManager(
        global_dir=tmp_path / "config",
        project_dir=tmp_path / "project-config",
        cli_overrides=(),
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "project-config").mkdir()
    await config.startup()

    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    return KernelModuleTable(flags=flags, config=config, state_dir=state_dir)


@pytest.mark.anyio
async def test_startup_registers_all_six_builtins(
    module_table: KernelModuleTable,
) -> None:
    mgr = ToolManager(module_table)
    await mgr.startup()

    for name in ("Bash", "FileRead", "FileEdit", "FileWrite", "Glob", "Grep", "ToolSearch"):
        assert mgr.lookup(name) is not None, f"missing {name}"


@pytest.mark.anyio
async def test_snapshot_for_session_emits_schemas(
    module_table: KernelModuleTable,
) -> None:
    mgr = ToolManager(module_table)
    await mgr.startup()

    snap = mgr.snapshot_for_session(session_id="s-1")
    names = [s.name for s in snap.schemas]
    assert sorted(names) == [
        "Agent", "Bash", "FileEdit", "FileRead", "FileWrite",
        "Glob", "Grep", "Python", "SendMessage", "Skill", "TaskOutput", "TaskStop",
        "TodoWrite", "ToolSearch",
    ]


@pytest.mark.anyio
async def test_snapshot_excludes_mutating_in_plan_mode(
    module_table: KernelModuleTable,
) -> None:
    mgr = ToolManager(module_table)
    await mgr.startup()

    snap = mgr.snapshot_for_session(session_id="s-1", plan_mode=True)
    names = {s.name for s in snap.schemas}
    # Read / search tools pass through
    assert "FileRead" in names
    assert "Glob" in names
    # Mutating / execute tools are filtered
    assert "Bash" not in names
    assert "FileEdit" not in names
    assert "FileWrite" not in names


@pytest.mark.anyio
async def test_agent_survives_plan_mode(
    module_table: KernelModuleTable,
) -> None:
    """AgentTool (kind=orchestrate) must survive plan-mode filtering.

    CC parity: Agent stays visible in plan mode so session-specific
    guidance includes the agent/search/explore bullets.
    """
    mgr = ToolManager(module_table)
    await mgr.startup()

    snap = mgr.snapshot_for_session(session_id="s-1", plan_mode=True)
    schema_names = {s.name for s in snap.schemas}
    # Agent must be in schemas (LLM-visible), not just lookup — session
    # guidance uses schema names so agent bullets only appear when the
    # LLM can actually call the tool.
    assert "Agent" in schema_names, "AgentTool must be in schemas in plan-mode (kind=orchestrate)"
    assert "Agent" in snap.lookup


@pytest.mark.anyio
async def test_file_state_returns_shared_instance(
    module_table: KernelModuleTable,
) -> None:
    """Multiple calls return the same object so Tools share state."""
    mgr = ToolManager(module_table)
    await mgr.startup()
    assert mgr.file_state() is mgr.file_state()


@pytest.mark.anyio
async def test_shutdown_clears_file_state(
    module_table: KernelModuleTable,
    tmp_path: Path,
) -> None:
    mgr = ToolManager(module_table)
    await mgr.startup()

    p = tmp_path / "f.txt"
    p.write_text("x")
    mgr.file_state().record(p, "x")
    assert mgr.file_state().verify(p) is not None

    await mgr.shutdown()
    assert mgr.file_state().verify(p) is None


@pytest.mark.anyio
async def test_startup_injects_prompt_manager_into_every_tool(
    module_table: KernelModuleTable,
    tmp_path: Path,
) -> None:
    """Every registered tool must receive the live PromptManager.

    This is the contract ToolManager promises so tools with
    ``description_key`` can resolve text at schema time.
    """
    pm_root = tmp_path / "prompts"
    pm_root.mkdir()
    pm = PromptManager(defaults_dir=pm_root)
    pm.load()
    module_table.prompts = pm

    mgr = ToolManager(module_table)
    await mgr.startup()

    for tool, _layer in mgr._registry.all_tools():
        assert tool._prompt_manager is pm, (
            f"tool {tool.name} did not receive PromptManager"
        )


@pytest.mark.anyio
async def test_tool_schema_description_resolves_from_prompt_manager(
    module_table: KernelModuleTable,
    tmp_path: Path,
) -> None:
    """End-to-end: a tool with description_key sees its text file
    content through to_schema() after ToolManager startup.
    """
    # Seed a tools/bash.txt that overrides whatever BashTool ships with.
    pm_root = tmp_path / "prompts"
    tools_dir = pm_root / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "bash.txt").write_text(
        "E2E-VERIFY: this text came from PromptManager", encoding="utf-8"
    )
    pm = PromptManager(defaults_dir=pm_root)
    pm.load()
    module_table.prompts = pm

    mgr = ToolManager(module_table)
    await mgr.startup()

    # Opt-in: assign description_key so the Bash tool consults PromptManager.
    bash = mgr.lookup("Bash")
    assert bash is not None
    bash.description_key = "tools/bash"

    schema = bash.to_schema()
    assert schema.description == "E2E-VERIFY: this text came from PromptManager"
