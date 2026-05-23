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
    assert cmd.aliases == []
    assert cmd.metadata == {}


# ---------------------------------------------------------------------------
# CommandRegistry
# ---------------------------------------------------------------------------


def test_registry_register_and_lookup() -> None:
    reg = CommandRegistry()
    cmd = CommandDef(
        name="skill:debug",
        description="Debug",
        usage="/skill:debug",
        acp_method="m/skill",
        aliases=["debug"],
    )
    reg.register(cmd)
    assert reg.lookup("skill:debug") is cmd
    assert reg.lookup("debug") is cmd


def test_registry_lookup_unknown_returns_none() -> None:
    reg = CommandRegistry()
    assert reg.lookup("nonexistent") is None


def test_registry_duplicate_raises() -> None:
    reg = CommandRegistry()
    cmd = CommandDef(name="x", description="d", usage="/x", acp_method=None)
    reg.register(cmd)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(cmd)


def test_registry_alias_collision_raises() -> None:
    reg = CommandRegistry()
    reg.register(CommandDef(name="model", description="d", usage="/model", acp_method=None))
    with pytest.raises(ValueError, match="alias already registered|already registered"):
        reg.register(
            CommandDef(
                name="skill:model",
                description="d",
                usage="/skill:model",
                acp_method=None,
                aliases=["model"],
            )
        )


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
        "flag",
        "secrets",
        "agents",
        "agent",
        "gateways",
        "skills",
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
    assert mgr.lookup("flag").acp_method == MustangMethod.FLAGS_LIST
    assert mgr.lookup("flags") is None
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


async def test_command_manager_skills_builtin_uses_skills_list(
    module_table: MagicMock,
) -> None:
    mgr = CommandManager(module_table)
    await mgr.startup()

    skills = mgr.lookup("skills")
    assert skills is not None
    assert skills.acp_method == MustangMethod.SKILLS_LIST
    assert "install" in skills.subcommands
    assert "sources" in skills.subcommands


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

    cmd = mgr.lookup("skill:debug")
    assert cmd is not None
    assert cmd.source == "skill"
    assert cmd.description == "Debug workflow"
    assert cmd.usage == "/skill:debug <target>"
    assert cmd.acp_method == MustangMethod.SESSION_ACTIVATE_SKILL
    assert cmd.metadata["skillName"] == "debug"
    assert cmd.aliases == ["debug"]
    assert mgr.lookup("debug") is cmd
    assert [c.name for c in mgr.list_commands()].count("debug") == 0


async def test_command_manager_does_not_let_skills_shadow_builtins() -> None:
    skills = _FakeSkillManager([_skill("model", "Should not replace builtin")])
    mgr = CommandManager(_FakeModuleTable(skills))  # type: ignore[arg-type]

    await mgr.startup()

    cmd = mgr.lookup("model")
    assert cmd is not None
    assert cmd.source == "builtin"
    assert cmd.description != "Should not replace builtin"
    skill_cmd = mgr.lookup("skill:model")
    assert skill_cmd is not None
    assert skill_cmd.source == "skill"
    assert "model" not in skill_cmd.aliases


async def test_command_manager_refreshes_when_skills_change() -> None:
    skills = _FakeSkillManager([_skill("alpha")])
    mgr = CommandManager(_FakeModuleTable(skills))  # type: ignore[arg-type]
    seen: list[list[dict]] = []

    await mgr.startup()
    mgr.on_commands_changed(lambda commands: seen.append(commands))
    skills.skills = [_skill("beta")]
    skills.callbacks[0]()

    assert mgr.lookup("skill:alpha") is None
    assert mgr.lookup("skill:beta") is not None
    assert seen
