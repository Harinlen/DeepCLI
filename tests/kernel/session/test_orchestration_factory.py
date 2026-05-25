from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from kernel.agents.mustang.llm.config import ModelRef
from kernel.agents.mustang.sessions.orchestration.factory import SessionOrchestratorFactoryMixin
from kernel.agents.mustang.sessions.runtime.state import Session


class _ModuleTable:
    def __init__(self, by_name: dict[str, Any]) -> None:
        self.by_name = by_name
        self.prompts = _Prompts()

    def get(self, cls: type) -> Any:
        value = self.by_name.get(cls.__name__)
        if value is None:
            raise KeyError(cls.__name__)
        return value


class _LLM:
    def model_for(self, role: str) -> ModelRef:
        assert role == "default"
        return ModelRef(provider="test", model="default")

    def model_for_or_default(self, role: str) -> ModelRef:
        assert role == "compact"
        return ModelRef(provider="test", model="compact")


class _NoDefaultLLM:
    def model_for(self, role: str) -> ModelRef:
        assert role == "default"
        raise KeyError("No model assigned for role: 'default'")

    def model_for_or_default(self, role: str) -> ModelRef:
        raise KeyError("No model assigned for role: 'default'")


class _Prompts:
    def get(self, name: str) -> str:
        return f"prompt:{name}"


class _Authorizer:
    def __init__(self) -> None:
        self.opened: list[str] = []

    def on_session_open(self, session_id: str) -> None:
        self.opened.append(session_id)


class _MCP:
    def get_connected(self) -> list[Any]:
        return [
            SimpleNamespace(name="docs", instructions="Read docs first"),
            SimpleNamespace(name="empty", instructions=""),
        ]


class _Router:
    def __init__(self) -> None:
        self.frames: list[Any] = []

    def route_message(self, frame: Any) -> object:
        self.frames.append(frame)
        return object()


class _Factory(SessionOrchestratorFactoryMixin):
    def __init__(self, module_table: Any) -> None:
        self._module_table = module_table
        self._sessions: dict[str, Session] = {}
        self._agent_context = SimpleNamespace(agent_id="primary")

    def deliver_message(
        self,
        target_session_id: str,
        message: str,
        *,
        sender_session_id: str | None = None,
    ) -> bool:
        return (target_session_id, message, sender_session_id) == ("target", "hello", "s-1")


def _session(session_id: str, tmp_path: Path, *, senders: dict[str, Any] | None = None) -> Session:
    now = datetime.now(timezone.utc)
    orchestrator = SimpleNamespace(
        set_mode=lambda mode: setattr(orchestrator, "mode", mode),
        mode=None,
        _has_exited_plan_mode=False,
        _needs_plan_mode_exit_attachment=False,
        _plan_mode_turn_count=99,
        _plan_mode_attachment_count=99,
    )
    return Session(
        session_id=session_id,
        cwd=tmp_path,
        created_at=now,
        updated_at=now,
        title=None,
        git_branch=None,
        mode_id="default",
        config_options={},
        mcp_servers=[],
        orchestrator=orchestrator,  # type: ignore[arg-type]
        senders=senders or {},
    )


def test_optional_subsystem_returns_none_when_import_or_registration_missing() -> None:
    factory = _Factory(_ModuleTable({}))

    assert factory._optional_subsystem("kernel.nope", "Nope") is None
    assert factory._optional_subsystem("kernel.agents.mustang.tools", "MissingClass") is None
    assert factory._optional_subsystem("kernel.agents.mustang.tools", "ToolManager") is None


def test_make_orchestrator_wires_deps_and_session_bound_closures(tmp_path: Path) -> None:
    authorizer = _Authorizer()
    router = _Router()
    module_table = _ModuleTable(
        {
            "LLMManager": _LLM(),
            "ToolAuthorizer": authorizer,
            "MCPManager": _MCP(),
        }
    )
    module_table.agent_hub = SimpleNamespace(router=router)
    factory = _Factory(module_table)
    session = _session("s-1", tmp_path, senders={"conn": object()})
    factory._sessions["s-1"] = session

    orchestrator, task_registry = factory._make_orchestrator("s-1", tmp_path, [], None)
    deps = orchestrator._deps  # type: ignore[attr-defined]

    assert authorizer.opened == ["s-1"]
    assert deps.provider is module_table.by_name["LLMManager"]
    assert deps.authorizer is authorizer
    assert deps.prompts is module_table.prompts
    assert deps.module_table is module_table
    assert deps.task_registry is task_registry
    assert orchestrator._agent_context is factory._agent_context  # type: ignore[attr-defined]
    assert orchestrator._prompt_builder._agent_context is factory._agent_context  # type: ignore[attr-defined]
    assert deps.mcp_instructions() == [("docs", "Read docs first")]
    assert deps.should_avoid_prompts_provider() is False

    deps.queue_reminders(["remember this"])
    assert session.pending_reminders == ["remember this"]
    assert deps.drain_reminders() == ["remember this"]
    assert session.pending_reminders == []

    assert deps.deliver_cross_session("target", "hello") is True
    assert deps.route_agent_message("peer", "ping") is True
    routed = router.frames[0]
    assert routed.target.agent_id == "peer"
    assert routed.session_id == "s-1"
    assert routed.payload == {"text": "ping"}

    deps.set_mode("plan")
    assert session.pre_plan_mode == "default"
    assert session.mode_id == "plan"
    assert session.pending_mode_changes[-1] == ("default", "plan")
    assert session.orchestrator._plan_mode_turn_count == 0

    deps.set_mode("restore")
    assert session.mode_id == "default"
    assert session.has_exited_plan_mode is True
    assert session.needs_plan_mode_exit_attachment is True
    assert session.pending_mode_changes[-1] == ("plan", "default")


def test_make_orchestrator_degrades_when_optional_subsystems_are_missing(tmp_path: Path) -> None:
    factory = _Factory(_ModuleTable({"LLMManager": _LLM()}))

    orchestrator, _task_registry = factory._make_orchestrator("s-1", tmp_path, [], None)
    deps = orchestrator._deps  # type: ignore[attr-defined]

    assert deps.tool_source is None
    assert deps.authorizer is None
    assert deps.mcp_instructions() == []
    assert deps.route_agent_message("peer", "ping") is False
    assert deps.should_avoid_prompts_provider() is True


def test_make_orchestrator_allows_missing_default_model(tmp_path: Path) -> None:
    factory = _Factory(_ModuleTable({"LLMManager": _NoDefaultLLM()}))

    orchestrator, _task_registry = factory._make_orchestrator("s-1", tmp_path, [], None)

    assert orchestrator._config.model == ModelRef(provider="default", model="default")  # type: ignore[attr-defined]
