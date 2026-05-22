"""ToolManager subsystem — startup + snapshot integration."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any

import httpx
import pytest

from kernel.core.config import ConfigManager
from kernel.core.flags import FlagManager
from kernel.core.secrets import SecretManager
from kernel.agents.mustang.module_table import KernelModuleTable
from kernel.agents.mustang.prompts.manager import PromptManager
from kernel.agents.mustang.tools import ToolManager
import kernel.agents.mustang.tools as tools_mod
from kernel.agents.mustang.tools.web import management as web_management


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

    for name in (
        "Bash",
        "Read",
        "FileRead",
        "Edit",
        "FileEdit",
        "Write",
        "FileWrite",
        "Glob",
        "Grep",
        "ToolSearch",
    ):
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
        "Agent",
        "Bash",
        "Edit",
        "Glob",
        "Grep",
        "Python",
        "Read",
        "RestartSelf",
        "SendMessage",
        "Skill",
        "TaskOutput",
        "TaskStop",
        "TodoWrite",
        "ToolSearch",
        "Write",
    ]


@pytest.mark.anyio
async def test_snapshot_keeps_mutating_tools_visible_in_plan_mode(
    module_table: KernelModuleTable,
) -> None:
    mgr = ToolManager(module_table)
    await mgr.startup()

    snap = mgr.snapshot_for_session(session_id="s-1", plan_mode=True)
    names = {s.name for s in snap.schemas}
    assert "Read" in names
    assert "Glob" in names
    assert "Bash" in names
    assert "Edit" in names
    assert "Write" in names


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
async def test_web_fetch_external_backend_prompts_then_stores_valid_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    secrets = SecretManager(db_path=tmp_path / "secrets.db")
    await secrets.startup()
    mt = KernelModuleTable(
        flags=flags,
        config=config,
        state_dir=tmp_path / "state",
        secrets=secrets,
    )
    (tmp_path / "state").mkdir()
    mgr = ToolManager(mt)
    try:
        await mgr.startup()
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)

        missing = await mgr.set_web_fetch_backend("tavily")
        assert missing["credentialRequired"] is True
        assert missing["credentialRequest"]["envKey"] == "TAVILY_API_KEY"

        class _Client:
            def __init__(self, **_: Any) -> None:
                pass

            async def __aenter__(self) -> "_Client":
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def post(self, url: str, **kwargs: Any) -> httpx.Response:
                assert url == "https://api.tavily.com/extract"
                assert kwargs["headers"]["Authorization"] == "Bearer tvly-valid"
                assert "api_key" not in kwargs["json"]
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "url": "https://example.com",
                                "content": "Example Domain",
                                "title": "Example Domain",
                            }
                        ]
                    },
                    request=httpx.Request("POST", url),
                )

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(**kwargs))

        selected = await mgr.set_web_fetch_backend("tavily", api_key="tvly-valid")

        assert selected["backend"] == "tavily"
        assert selected["credentialRequired"] is False
        assert secrets.get("web_fetch.tavily.api_key") == "tvly-valid"
        config_model = mgr.web_fetch_config_model()
        assert config_model.backend == "tavily"
        api_key_ref = config_model.backends["tavily"]["api_key_ref"]
        assert api_key_ref.startswith("secret:")
        assert secrets.get(api_key_ref) == "tvly-valid"
        public_config = mgr.web_fetch_config()
        assert public_config["backends"]["tavily"]["api_key"] == "configured"
        assert "api_key_ref" not in public_config["backends"]["tavily"]

        class _UnexpectedClient:
            def __init__(self, **_: Any) -> None:
                pass

            async def __aenter__(self) -> "_UnexpectedClient":
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def post(self, *_: Any, **__: Any) -> httpx.Response:
                raise AssertionError("current backend selection must not revalidate credentials")

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _UnexpectedClient(**kwargs))

        unchanged = await mgr.set_web_fetch_backend("tavily")
        assert unchanged["backend"] == "tavily"
        assert unchanged["changed"] is False
        assert unchanged["credentialRequired"] is False
    finally:
        await mgr.shutdown()
        secrets.close()


@pytest.mark.anyio
async def test_web_fetch_external_backend_validates_existing_api_key_before_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    secrets = SecretManager(db_path=tmp_path / "secrets.db")
    await secrets.startup()
    mt = KernelModuleTable(
        flags=flags,
        config=config,
        state_dir=tmp_path / "state",
        secrets=secrets,
    )
    (tmp_path / "state").mkdir()
    mgr = ToolManager(mt)
    try:
        await mgr.startup()
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-invalid")

        class _Client:
            def __init__(self, **_: Any) -> None:
                pass

            async def __aenter__(self) -> "_Client":
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def post(self, url: str, **kwargs: Any) -> httpx.Response:
                status = 200 if kwargs["headers"]["Authorization"] == "Bearer tvly-valid" else 401
                body = (
                    {
                        "results": [
                            {
                                "url": "https://example.com",
                                "content": "Example Domain",
                                "title": "Example Domain",
                            }
                        ]
                    }
                    if status == 200
                    else {"error": "unauthorized"}
                )
                return httpx.Response(status, json=body, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(**kwargs))

        invalid = await mgr.set_web_fetch_backend("tavily")
        assert invalid["changed"] is False
        assert invalid["credentialRequired"] is True
        assert "Tavily Extract API returned HTTP 401" in invalid["message"]
        assert "developer.mozilla.org" not in invalid["message"]
        assert mgr.web_fetch_config_model().backend == "auto"

        selected = await mgr.set_web_fetch_backend("tavily", api_key="tvly-valid")
        assert selected["backend"] == "tavily"
        assert selected["credentialRequired"] is False
        assert secrets.get("web_fetch.tavily.api_key") == "tvly-valid"
        assert os.environ["TAVILY_API_KEY"] == "tvly-valid"
    finally:
        await mgr.shutdown()
        secrets.close()


@pytest.mark.anyio
async def test_web_fetch_backend_options_show_configured_for_unvalidated_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    secrets = SecretManager(db_path=tmp_path / "secrets.db")
    await secrets.startup()
    mt = KernelModuleTable(
        flags=flags,
        config=config,
        state_dir=tmp_path / "state",
        secrets=secrets,
    )
    (tmp_path / "state").mkdir()
    mgr = ToolManager(mt)
    try:
        await mgr.startup()
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-configured")

        options = mgr.web_fetch_backend_options()["options"]
        tavily = next(option for option in options if option["id"] == "tavily")

        assert tavily["status"] == "configured"
        assert tavily["hasCredentials"] is True
        assert tavily["available"] is False
        assert tavily["credentialRequired"] is False
    finally:
        await mgr.shutdown()
        secrets.close()


@pytest.mark.anyio
async def test_web_fetch_config_api_key_validates_and_hides_secret_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    secrets = SecretManager(db_path=tmp_path / "secrets.db")
    await secrets.startup()
    mt = KernelModuleTable(
        flags=flags,
        config=config,
        state_dir=tmp_path / "state",
        secrets=secrets,
    )
    (tmp_path / "state").mkdir()
    mgr = ToolManager(mt)
    try:
        await mgr.startup()
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)

        class _Client:
            def __init__(self, **_: Any) -> None:
                pass

            async def __aenter__(self) -> "_Client":
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def post(self, url: str, **kwargs: Any) -> httpx.Response:
                assert kwargs["headers"]["Authorization"] == "Bearer tvly-config"
                assert "api_key" not in kwargs["json"]
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "url": "https://example.com",
                                "content": "Example Domain",
                                "title": "Example Domain",
                            }
                        ]
                    },
                    request=httpx.Request("POST", url),
                )

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(**kwargs))

        public_config = await mgr.set_web_fetch_config_value("tavily.api_key", "tvly-config")

        assert secrets.get("web_fetch.tavily.api_key") == "tvly-config"
        api_key_ref = mgr.web_fetch_config_model().backends["tavily"]["api_key_ref"]
        assert api_key_ref.startswith("secret:")
        assert secrets.get(api_key_ref) == "tvly-config"
        assert public_config["backends"]["tavily"]["api_key"] == "configured"
        assert "api_key_ref" not in public_config["backends"]["tavily"]
        assert mgr.web_fetch_config_model().backend == "auto"
    finally:
        await mgr.shutdown()
        secrets.close()


@pytest.mark.anyio
async def test_web_fetch_backend_setup_path_runs_allowlisted_setup(
    module_table: KernelModuleTable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_called = False

    def fake_installed(definition: Any) -> bool:
        return definition.id != "crawl4ai" or setup_called

    def fake_available(definition: Any) -> bool:
        if definition.id in {"auto", "httpx"}:
            return True
        if definition.id == "crawl4ai":
            return setup_called
        return False

    monkeypatch.setattr(tools_mod, "backend_is_installed", fake_installed)
    monkeypatch.setattr(tools_mod, "backend_is_available", fake_available)

    mgr = ToolManager(module_table)
    await mgr.startup()

    async def fake_setup(definition: Any) -> dict[str, Any]:
        nonlocal setup_called
        assert definition.id == "crawl4ai"
        setup_called = True
        return {"ok": True, "logs": [{"command": "fake crawl4ai setup", "exitCode": 0}]}

    monkeypatch.setattr(mgr, "_run_web_fetch_setup", fake_setup)

    missing = await mgr.set_web_fetch_backend("crawl4ai")
    assert missing["setupRequired"] is True
    assert setup_called is False

    selected = await mgr.set_web_fetch_backend("crawl4ai", run_setup=True)
    assert setup_called is True
    assert selected["backend"] == "crawl4ai"
    assert selected["setupRequired"] is False
    assert mgr.web_fetch_config_model().backend == "crawl4ai"


@pytest.mark.anyio
async def test_web_fetch_backend_run_setup_repairs_selected_backend(
    module_table: KernelModuleTable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_calls = 0

    monkeypatch.setattr(
        tools_mod,
        "backend_is_installed",
        lambda definition: definition.id != "crawl4ai" or setup_calls > 0,
    )
    monkeypatch.setattr(
        tools_mod,
        "backend_is_available",
        lambda definition: definition.id in {"auto", "httpx"} or setup_calls > 0,
    )

    mgr = ToolManager(module_table)
    await mgr.startup()
    await mgr._update_web_fetch_config(
        mgr.web_fetch_config_model().model_copy(update={"backend": "crawl4ai"})
    )

    async def fake_setup(definition: Any) -> dict[str, Any]:
        nonlocal setup_calls
        assert definition.id == "crawl4ai"
        setup_calls += 1
        return {"ok": True, "logs": [{"command": "fake browser install", "exitCode": 0}]}

    monkeypatch.setattr(mgr, "_run_web_fetch_setup", fake_setup)

    repaired = await mgr.set_web_fetch_backend("crawl4ai", run_setup=True)

    assert setup_calls == 1
    assert repaired["backend"] == "crawl4ai"
    assert repaired["setupResult"]["ok"] is True
    assert mgr.web_fetch_config_model().backend == "crawl4ai"


def test_web_fetch_setup_installs_with_uv_into_current_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_management, "_resolve_uv", lambda: "/tmp/uv")

    command = web_management._python_package_install_command("crawl4ai>=0.6.3")

    assert command == (
        "/tmp/uv",
        "pip",
        "install",
        "--python",
        sys.executable,
        "crawl4ai>=0.6.3",
    )


def test_web_fetch_crawl4ai_setup_plan_has_no_system_dependency_install() -> None:
    definition = web_management.get_definition("crawl4ai")
    assert definition is not None

    plan = web_management.build_setup_plan(definition)
    commands = "\n".join(plan["commands"])

    assert "crawl4ai-setup" not in commands
    assert "--with-deps" not in commands
    assert "sudo" not in commands
    assert "--target" in commands
    assert ".deepcli/packages/crawl4ai" in commands
    assert "playwright install chromium" in commands
    assert "patchright install chromium" in commands


@pytest.mark.anyio
async def test_web_fetch_setup_runs_noninteractive_with_deepcli_env(tmp_path: Path) -> None:
    code = (
        "import os, sys; "
        "assert os.environ['CRAWL4_AI_BASE_DIRECTORY'] == sys.argv[1]; "
        "assert os.environ['PLAYWRIGHT_BROWSERS_PATH'].startswith(sys.argv[1]); "
        "assert os.environ['PYTHONPATH'].split(os.pathsep)[0].endswith('packages/crawl4ai'); "
        "assert sys.stdin.read() == ''"
    )
    definition = web_management.BackendDefinition(
        id="crawl4ai",
        label="Crawl4AI",
        category="test",
        cost="test",
        role="test",
        setup_commands=((sys.executable, "-c", code, str(tmp_path)),),
        python_paths=(str(tmp_path / "packages" / "crawl4ai"),),
        setup_env={
            "CRAWL4_AI_BASE_DIRECTORY": str(tmp_path),
            "PLAYWRIGHT_BROWSERS_PATH": str(tmp_path / "cache" / "ms-playwright"),
        },
    )

    result = await web_management.run_setup(definition)

    assert result["ok"] is True


@pytest.mark.anyio
async def test_web_fetch_backend_setup_failure_includes_command_output(
    module_table: KernelModuleTable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tools_mod,
        "backend_is_installed",
        lambda definition: definition.id != "crawl4ai",
    )
    monkeypatch.setattr(
        tools_mod,
        "backend_is_available",
        lambda definition: definition.id in {"auto", "httpx"},
    )

    mgr = ToolManager(module_table)
    await mgr.startup()

    async def fake_setup(definition: Any) -> dict[str, Any]:
        assert definition.id == "crawl4ai"
        return {
            "ok": False,
            "logs": [
                {
                    "command": "uv pip install --python /deepcli/python 'crawl4ai>=0.6.3'",
                    "exitCode": 1,
                    "stderr": "No matching distribution found for crawl4ai",
                }
            ],
        }

    monkeypatch.setattr(mgr, "_run_web_fetch_setup", fake_setup)

    failed = await mgr.set_web_fetch_backend("crawl4ai", run_setup=True)

    assert failed["setupRequired"] is True
    assert "uv pip install --python /deepcli/python" in failed["message"]
    assert "No matching distribution found" in failed["message"]
    assert failed["setupResult"]["logs"][0]["exitCode"] == 1


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
        assert tool._prompt_manager is pm, f"tool {tool.name} did not receive PromptManager"


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
