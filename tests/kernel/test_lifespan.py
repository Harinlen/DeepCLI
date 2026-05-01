"""Lifespan-level tests for the kernel FastAPI app.

These tests verify the contract the lifespan is supposed to deliver:

- the two bootstrap services (``FlagManager``, ``ConfigManager``)
  come up before any regular subsystem and land on the shared
  :class:`~kernel.module_table.KernelModuleTable`
- regular subsystems are started through ``Subsystem.load`` in
  declared order, register themselves on the module table, and
  stop in reverse order at shutdown
- optional subsystems respect ``KernelFlags`` and disabled ones are
  neither started nor stopped
- a failed optional subsystem keeps the rest of the boot sequence
  running (degraded mode)
- a bootstrap-service failure aborts the kernel

Every assertion goes through ``app.state.module_table`` — that is
the single handle routes / handlers use at runtime, so exercising
it here catches regressions that would otherwise only surface at
request time.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kernel import app as app_module
from kernel.flags import FlagManager
from kernel.module_table import KernelModuleTable
from kernel.subsystem import Subsystem


class _Recorder:
    """Shared ledger to record subsystem startup/shutdown order."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.stopped: list[str] = []


@pytest.fixture
def tmp_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect flags file, config dirs, and state dir into ``tmp_path``.

    Without this the lifespan would read/write the real
    ``~/.mustang/`` tree during a test run, which is both a hermetic
    violation and a risk to the developer's actual tokens.
    """
    flags_path = tmp_path / "flags.yaml"
    global_dir = tmp_path / "config"
    project_dir = tmp_path / "project-config"
    state_dir = tmp_path / "state"
    global_dir.mkdir()
    project_dir.mkdir()

    monkeypatch.setattr(app_module, "FlagManager", lambda: FlagManager(path=flags_path))

    # SecretManager — redirect db to tmp_path.
    from kernel.secrets import SecretManager

    monkeypatch.setattr(
        app_module, "SecretManager",
        lambda: SecretManager(db_path=tmp_path / "secrets.db"),
    )

    original_config = app_module.ConfigManager

    def _config_factory(**kwargs: object) -> object:
        return original_config(
            global_dir=global_dir,
            project_dir=project_dir,
            cli_overrides=(),
            **kwargs,
        )

    monkeypatch.setattr(app_module, "ConfigManager", _config_factory)

    # ``Path.home()`` is what the lifespan uses to derive ``state_dir``;
    # point it at ``tmp_path`` for the duration of the test so the
    # real user home is never touched.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return state_dir


_SUBSYSTEM_ATTRS: dict[str, str] = {
    "auth": "ConnectionAuthenticator",
    "tool_authz": "ToolAuthorizer",
    "provider": "LLMProviderManager",
    "llm": "LLMManager",
    "tools": "ToolManager",
    "skills": "SkillManager",
    "hooks": "HookManager",
    "mcp": "MCPManager",
    "memory": "MemoryManager",
    "git": "GitManager",
    "session": "SessionManager",
    "commands": "CommandManager",
    "gateways": "GatewayManager",
}


@pytest.fixture
def fake_subsystems(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """Replace every regular subsystem in ``kernel.app`` with a recorder stub.

    Bootstrap services (``FlagManager`` / ``ConfigManager``) are
    deliberately **not** replaced here — they have richer APIs than
    the ``Subsystem`` contract and swapping them for a fake would
    either have to re-implement ``bind_section`` or break the
    subsystems that rely on it.  ``tmp_state`` handles their
    isolation instead.
    """
    recorder = _Recorder()

    def _make(subsystem_name: str) -> type[Subsystem]:
        class _Fake(Subsystem):
            async def startup(self) -> None:
                recorder.started.append(subsystem_name)

            async def shutdown(self) -> None:
                recorder.stopped.append(subsystem_name)

        _Fake.__name__ = f"Fake_{subsystem_name}"
        return _Fake

    for subsystem_name, attr in _SUBSYSTEM_ATTRS.items():
        monkeypatch.setattr(app_module, attr, _make(subsystem_name))

    # _CORE_SUBSYSTEMS / _OPTIONAL_SUBSYSTEMS / _TRAILING_SUBSYSTEM
    # were built at import time from the real classes, so rebuild
    # them now so the lifespan picks up the fakes.
    monkeypatch.setattr(
        app_module,
        "_CORE_SUBSYSTEMS",
        [
            ("auth", app_module.ConnectionAuthenticator),
            ("tool_authz", app_module.ToolAuthorizer),
            ("provider", app_module.LLMProviderManager),
            ("llm", app_module.LLMManager),
        ],
    )
    monkeypatch.setattr(
        app_module,
        "_OPTIONAL_SUBSYSTEMS",
        [
            ("tools", app_module.ToolManager),
            ("skills", app_module.SkillManager),
            ("hooks", app_module.HookManager),
            ("mcp", app_module.MCPManager),
            ("memory", app_module.MemoryManager),
            ("git", app_module.GitManager),
        ],
    )
    monkeypatch.setattr(
        app_module,
        "_TRAILING_SUBSYSTEMS",
        [
            ("session", app_module.SessionManager),
            ("commands", app_module.CommandManager),
            ("gateways", app_module.GatewayManager),
        ],
    )

    return recorder


def _lifespan_ctx(app: object):
    """Directly invoke the FastAPI lifespan context manager.

    Bypasses HTTP transport — the lifespan is what we want to test,
    not request routing.
    """
    return app.router.lifespan_context(app)  # type: ignore[attr-defined]


async def _run_lifespan(app: object) -> None:
    async with _lifespan_ctx(app):
        pass


def _names(subsystems: list[Subsystem]) -> list[str]:
    return [getattr(s, "_lifecycle_name", "") for s in subsystems]


async def test_lifespan_starts_all_subsystems_by_default(
    tmp_state: Path, fake_subsystems: _Recorder
) -> None:
    app = app_module.create_app()
    await _run_lifespan(app)

    # Every regular subsystem came up in declared order.
    assert fake_subsystems.started == [
        "auth",
        "tool_authz",
        "provider",
        "llm",
        "tools",
        "skills",
        "hooks",
        "mcp",
        "memory",
        "git",
        "session",
        "commands",
        "gateways",
    ]
    # And stopped in reverse order.  Bootstrap services do not have
    # a teardown step and so never appear in the ledger.
    assert fake_subsystems.stopped == [
        "gateways",
        "commands",
        "session",
        "git",
        "memory",
        "mcp",
        "hooks",
        "skills",
        "tools",
        "llm",
        "provider",
        "tool_authz",
        "auth",
    ]


async def test_lifespan_skips_disabled_optional_subsystems(
    tmp_state: Path, fake_subsystems: _Recorder, tmp_path: Path
) -> None:
    (tmp_path / "flags.yaml").write_text(
        yaml.safe_dump({"kernel": {"memory": False, "mcp": False, "tools": True}})
    )

    app = app_module.create_app()
    await _run_lifespan(app)

    assert "memory" not in fake_subsystems.started
    assert "mcp" not in fake_subsystems.started
    assert "tools" in fake_subsystems.started
    # Session still comes last among the started ones.
    assert fake_subsystems.started[-1] == "gateways"
    # Disabled subsystems also don't show up in shutdown.
    assert "memory" not in fake_subsystems.stopped
    assert "mcp" not in fake_subsystems.stopped


async def test_module_table_holds_bootstrap_services(
    tmp_state: Path, fake_subsystems: _Recorder
) -> None:
    """Flags / config / state_dir must be reachable on the module table."""
    app = app_module.create_app()
    async with _lifespan_ctx(app):
        table: KernelModuleTable = app.state.module_table

        assert isinstance(table.flags, FlagManager)
        assert table.config is not None
        assert table.prompts is not None
        assert table.state_dir.exists()
        # And state_dir was created with the restricted permission
        # when the lifespan synthesized it.
        import stat

        assert (stat.S_IMODE(table.state_dir.stat().st_mode) & 0o077) == 0
        # Flags can be interrogated for section data.
        kernel_flags = table.flags.get_section("kernel")
        assert kernel_flags.memory is True


async def test_module_table_registers_regular_subsystems_in_order(
    tmp_state: Path, fake_subsystems: _Recorder
) -> None:
    """``KernelModuleTable.subsystems()`` returns the load sequence."""
    app = app_module.create_app()
    async with _lifespan_ctx(app):
        table: KernelModuleTable = app.state.module_table
        # All 13 regular subsystems present, in load order.
        assert _names(table.subsystems()) == [
            "auth",
            "tool_authz",
            "provider",
            "llm",
            "tools",
            "skills",
            "hooks",
            "mcp",
            "memory",
            "git",
            "session",
            "commands",
            "gateways",
        ]


async def test_lifespan_optional_startup_failure_is_degraded(
    tmp_state: Path,
    fake_subsystems: _Recorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An optional subsystem's startup() blowing up must not abort kernel boot."""

    class _BrokenTools(Subsystem):
        async def startup(self) -> None:
            raise RuntimeError("boom")

        async def shutdown(self) -> None:  # pragma: no cover
            raise AssertionError("shutdown called for a subsystem that never started")

    monkeypatch.setattr(
        app_module,
        "_OPTIONAL_SUBSYSTEMS",
        [
            ("tools", _BrokenTools),
            ("skills", app_module.SkillManager),
            ("hooks", app_module.HookManager),
            ("mcp", app_module.MCPManager),
            ("memory", app_module.MemoryManager),
        ],
    )

    app = app_module.create_app()
    await _run_lifespan(app)

    # tools failed to start → absent from ledger, but subsequent
    # subsystems still came up.
    assert "tools" not in fake_subsystems.started
    assert "skills" in fake_subsystems.started
    assert "session" in fake_subsystems.started
    # And nothing tried to shut down the broken tools instance.
    assert "tools" not in fake_subsystems.stopped


async def test_lifespan_config_failure_aborts(
    tmp_state: Path,
    fake_subsystems: _Recorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ConfigManager.startup() raises, kernel boot aborts with the error."""

    class _BrokenConfig:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def startup(self) -> None:
            raise RuntimeError("config is toast")

    monkeypatch.setattr(app_module, "ConfigManager", _BrokenConfig)

    app = app_module.create_app()
    with pytest.raises(RuntimeError, match="config is toast"):
        async with _lifespan_ctx(app):
            pass
