"""Tests for CommandManager, CommandRegistry, and CommandDef."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kernel.agents.mustang.commands import CommandManager, CommandDef, CommandRegistry
from kernel.core.protocol.acp.namespaces import MustangMethod


# ---------------------------------------------------------------------------
# CommandDef
# ---------------------------------------------------------------------------


def test_command_def_is_frozen() -> None:
    cmd = CommandDef(name="help", description="Help", usage="/help", acp_method=None)
    with pytest.raises((AttributeError, TypeError)):
        cmd.name = "other"  # type: ignore[misc]


def test_command_def_defaults() -> None:
    cmd = CommandDef(name="x", description="d", usage="/x", acp_method="m/foo")
    assert cmd.subcommands == []


# ---------------------------------------------------------------------------
# CommandRegistry
# ---------------------------------------------------------------------------


def test_registry_register_and_lookup() -> None:
    reg = CommandRegistry()
    cmd = CommandDef(name="model", description="List models", usage="/model", acp_method="m/list")
    reg.register(cmd)
    assert reg.lookup("model") is cmd


def test_registry_lookup_unknown_returns_none() -> None:
    reg = CommandRegistry()
    assert reg.lookup("nonexistent") is None


def test_registry_duplicate_raises() -> None:
    reg = CommandRegistry()
    cmd = CommandDef(name="x", description="d", usage="/x", acp_method=None)
    reg.register(cmd)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(cmd)


def test_registry_list_commands_order() -> None:
    reg = CommandRegistry()
    names = ["alpha", "beta", "gamma"]
    for n in names:
        reg.register(CommandDef(name=n, description="d", usage=f"/{n}", acp_method=None))
    assert [c.name for c in reg.list_commands()] == names


# ---------------------------------------------------------------------------
# CommandManager (Subsystem)
# ---------------------------------------------------------------------------


@pytest.fixture
def module_table() -> MagicMock:
    mt = MagicMock()
    # FlagManager.register returns a frozen Pydantic model; not used by
    # CommandManager, but the base Subsystem constructor stores module_table.
    return mt


async def test_command_manager_startup_registers_builtins(
    module_table: MagicMock,
) -> None:
    mgr = CommandManager(module_table)
    await mgr.startup()

    cmds = mgr.list_commands()
    names = [c.name for c in cmds]
    # All documented built-in commands must be present.
    for expected in (
        "help",
        "model",
        "plan",
        "compact",
        "session",
        "cost",
        "memory",
        "kernel",
        "global",
        "flags",
        "secrets",
        "agents",
        "agent",
        "gateways",
        "mcp",
    ):
        assert expected in names, f"Expected built-in command {expected!r} missing"
    assert "cron" in names
    assert "auth" not in names


async def test_command_manager_lookup_hit(module_table: MagicMock) -> None:
    mgr = CommandManager(module_table)
    await mgr.startup()

    cmd = mgr.lookup("model")
    assert cmd is not None
    assert cmd.acp_method == MustangMethod.MODEL_PROFILE_LIST


async def test_command_manager_cost_uses_namespaced_method(module_table: MagicMock) -> None:
    mgr = CommandManager(module_table)
    await mgr.startup()

    cmd = mgr.lookup("cost")
    assert cmd is not None
    assert cmd.acp_method == MustangMethod.SESSION_GET_USAGE


async def test_command_manager_kernel_uses_runtime_status_method(module_table: MagicMock) -> None:
    mgr = CommandManager(module_table)
    await mgr.startup()

    cmd = mgr.lookup("kernel")
    assert cmd is not None
    assert cmd.acp_method == MustangMethod.RUNTIME_STATUS


async def test_command_manager_global_flags_secrets_use_management_methods(
    module_table: MagicMock,
) -> None:
    mgr = CommandManager(module_table)
    await mgr.startup()

    assert mgr.lookup("global").acp_method == MustangMethod.GLOBAL_BACKUP
    assert mgr.lookup("flags").acp_method == MustangMethod.FLAGS_LIST
    assert mgr.lookup("secrets").acp_method == MustangMethod.SECRETS_LIST


async def test_command_manager_agents_and_gateways_use_management_methods(
    module_table: MagicMock,
) -> None:
    mgr = CommandManager(module_table)
    await mgr.startup()

    assert mgr.lookup("agents").acp_method == MustangMethod.AGENTS_LIST
    assert mgr.lookup("agent").acp_method == MustangMethod.AGENT_SEND
    gateways = mgr.lookup("gateways")
    assert gateways.acp_method == MustangMethod.GATEWAYS_LIST
    assert "create" in gateways.subcommands
    assert "delete" in gateways.subcommands


async def test_command_manager_mcp_uses_management_methods(
    module_table: MagicMock,
) -> None:
    mgr = CommandManager(module_table)
    await mgr.startup()

    mcp = mgr.lookup("mcp")
    assert mcp.acp_method == MustangMethod.MCP_LIST
    assert mcp.subcommands == ["list", "read", "create", "update", "delete"]


async def test_command_manager_lookup_miss(module_table: MagicMock) -> None:
    mgr = CommandManager(module_table)
    await mgr.startup()
    assert mgr.lookup("nonexistent") is None


async def test_command_manager_shutdown_is_noop(module_table: MagicMock) -> None:
    mgr = CommandManager(module_table)
    await mgr.startup()
    # shutdown must not raise
    await mgr.shutdown()


def _skill(name: str, description: str = "Skill description", argument_hint: str | None = None):
    return SimpleNamespace(
        manifest=SimpleNamespace(
            name=name,
            description=description,
            argument_hint=argument_hint,
        )
    )


class _FakeSkillManager:
    def __init__(self, skills: list[object]) -> None:
        self.skills = skills
        self.callbacks = []

    def user_invocable_skills(self) -> list[object]:
        return self.skills

    def on_skills_changed(self, callback):
        self.callbacks.append(callback)


class _FakeModuleTable:
    def __init__(self, skills: _FakeSkillManager) -> None:
        self.skills = skills

    def has(self, cls: object) -> bool:
        from kernel.agents.mustang.skills import SkillManager

        return cls is SkillManager

    def get(self, cls: object) -> object:
        from kernel.agents.mustang.skills import SkillManager

        if cls is SkillManager:
            return self.skills
        raise KeyError(cls)


async def test_command_manager_projects_user_invocable_skills_as_commands() -> None:
    skills = _FakeSkillManager([_skill("debug", "Debug workflow", "<target>")])
    mgr = CommandManager(_FakeModuleTable(skills))  # type: ignore[arg-type]

    await mgr.startup()

    cmd = mgr.lookup("debug")
    assert cmd is not None
    assert cmd.source == "skill"
    assert cmd.description == "Debug workflow"
    assert cmd.usage == "/debug <target>"
    assert cmd.acp_method is None


async def test_command_manager_does_not_let_skills_shadow_builtins() -> None:
    skills = _FakeSkillManager([_skill("model", "Should not replace builtin")])
    mgr = CommandManager(_FakeModuleTable(skills))  # type: ignore[arg-type]

    await mgr.startup()

    cmd = mgr.lookup("model")
    assert cmd is not None
    assert cmd.source == "builtin"
    assert cmd.description != "Should not replace builtin"


async def test_command_manager_refreshes_when_skills_change() -> None:
    skills = _FakeSkillManager([_skill("alpha")])
    mgr = CommandManager(_FakeModuleTable(skills))  # type: ignore[arg-type]
    seen: list[list[dict]] = []

    await mgr.startup()
    mgr.on_commands_changed(lambda commands: seen.append(commands))
    skills.skills = [_skill("beta")]
    skills.callbacks[0]()

    assert mgr.lookup("alpha") is None
    assert mgr.lookup("beta") is not None
    assert seen
