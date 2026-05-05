"""No-FastAPI SessionManager host for a durable Agent Runtime."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from kernel.agent_runtime.websocket_runtime import RuntimeClientPeer
from kernel.config import ConfigManager
from kernel.connection_auth import AuthContext
from kernel.flags import FlagManager, KernelFlags
from kernel.gateways import GatewayManager
from kernel.git import GitManager
from kernel.hooks import HookManager
from kernel.llm import LLMManager
from kernel.llm_provider import LLMProviderManager
from kernel.mcp import MCPManager
from kernel.memory import MemoryManager
from kernel.module_table import KernelModuleTable
from kernel.prompts import PromptManager
from kernel.protocol.acp.schemas.session import (
    CancelExecutionRequest,
    CancelNotification,
    CloseSessionRequest,
    ExecutePythonRequest,
    ExecuteShellRequest,
    GetUsageRequest,
    ListSessionsRequest,
    LoadSessionRequest,
    NewSessionRequest,
    PromptRequest,
    ResumeSessionRequest,
    SetSessionModeRequest,
)
from kernel.protocol.interfaces.contracts.connection_context import ConnectionContext
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.protocol.flags import ProtocolFlags
from kernel.routes.flags import TransportFlags
from kernel.secrets import SecretManager
from kernel.schedule import ScheduleManager
from kernel.session import SessionManager
from kernel.session.context import AgentContext
from kernel.skills import SkillManager
from kernel.subsystem import Subsystem
from kernel.tool_authz import ToolAuthorizer
from kernel.tools import ToolManager

logger = logging.getLogger(__name__)

_CORE_SUBSYSTEMS: tuple[tuple[str, type[Subsystem]], ...] = (
    ("tool_authz", ToolAuthorizer),
    ("provider", LLMProviderManager),
    ("llm", LLMManager),
)
_OPTIONAL_SUBSYSTEMS: tuple[tuple[str, type[Subsystem]], ...] = (
    ("mcp", MCPManager),
    ("tools", ToolManager),
    ("skills", SkillManager),
    ("hooks", HookManager),
    ("memory", MemoryManager),
    ("git", GitManager),
)
_TRAILING_SUBSYSTEMS: tuple[tuple[str, type[Subsystem]], ...] = (
    ("gateways", GatewayManager),
    ("schedule", ScheduleManager),
)


@dataclass
class CollectingRuntimeSender:
    """ClientSender replacement that stores runtime notifications."""

    notifications: list[tuple[str, BaseModel]] = field(default_factory=list)
    client_peer: RuntimeClientPeer | None = None

    async def notify(self, method: str, params: BaseModel) -> None:
        self.notifications.append((method, params))
        if self.client_peer is not None:
            await self.client_peer.request_client(
                method=method,
                params=params.model_dump(by_alias=True),
            )

    async def request(
        self,
        method: str,
        params: BaseModel,
        *,
        result_type: type[BaseModel],
        timeout: float | None = None,
    ) -> Any:
        if self.client_peer is None:
            raise RuntimeError(f"runtime client request not available: {method}")
        result = await self.client_peer.request_client(
            method=method,
            params=params.model_dump(by_alias=True),
            timeout=timeout,
        )
        return result_type.model_validate(result)

    def pending_request_ids(self) -> list[Any]:
        return []


class AgentSessionRuntimeService:
    """Owns real SessionManager/Orchestrator state inside an Agent Runtime."""

    def __init__(
        self,
        *,
        agent_id: str,
        state_dir: Path,
        workspace: Path,
    ) -> None:
        self.agent_id = agent_id
        self.state_dir = state_dir
        self.workspace = workspace
        self.module_table: KernelModuleTable | None = None
        self._session_manager: SessionManager | None = None
        self._connections: dict[str, tuple[ConnectionContext, CollectingRuntimeSender]] = {}

    async def startup(self) -> None:
        flags = FlagManager()
        await flags.initialize()
        flags.register("transport", TransportFlags)
        flags.register("protocol", ProtocolFlags)
        kernel_flags = flags.get_section("kernel")
        assert isinstance(kernel_flags, KernelFlags)

        secrets = SecretManager()
        await secrets.startup()
        config = ConfigManager(secret_resolver=secrets.get)
        await config.startup()
        prompts = PromptManager(user_dirs=_prompt_user_dirs(self.workspace))
        prompts.load()

        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        module_table = KernelModuleTable(
            flags=flags,
            config=config,
            state_dir=self.state_dir,
            secrets=secrets,
            prompts=prompts,
        )
        self.module_table = module_table

        for name, factory in _CORE_SUBSYSTEMS:
            await _load(module_table, name, factory)
        for name, factory in _OPTIONAL_SUBSYSTEMS:
            if not getattr(kernel_flags, name):
                logger.info("Agent runtime subsystem %s disabled via flags", name)
                continue
            await _load(module_table, name, factory)

        context = AgentContext(
            agent_id=self.agent_id,
            workspace=self.workspace,
            state_dir=self.state_dir,
            session_store_path=self.state_dir / "sessions" / "sessions.db",
        )
        session = SessionManager(module_table, agent_context=context)  # type: ignore[abstract]
        session._lifecycle_name = "session"
        await session.startup()
        module_table.register(session)
        self._session_manager = session

        for name, factory in _TRAILING_SUBSYSTEMS:
            if not getattr(kernel_flags, name):
                logger.info("Agent runtime subsystem %s disabled via flags", name)
                continue
            await _load(module_table, name, factory)

    async def shutdown(self) -> None:
        if self.module_table is None:
            return
        for subsystem in reversed(self.module_table.subsystems()):
            await subsystem.unload()

    async def new_session(self, params: NewSessionRequest) -> dict[str, Any]:
        manager = self._manager()
        sender = CollectingRuntimeSender()
        ctx = _handler_context(sender)
        result = await manager.new(ctx, _to_contract_new(params))
        self._connections[result.session_id] = (ctx.conn, sender)
        return {"sessionId": result.session_id}

    async def list_sessions(self, params: ListSessionsRequest) -> dict[str, Any]:
        manager = self._manager()
        sender = CollectingRuntimeSender()
        ctx = _handler_context(sender)
        result = await manager.list(ctx, _to_contract_list(params))
        return result.model_dump(by_alias=True)

    async def load_session(self, params: LoadSessionRequest) -> dict[str, Any]:
        manager = self._manager()
        sender = CollectingRuntimeSender()
        ctx = _handler_context(sender)
        result = await manager.load_session(ctx, _to_contract_load(params))
        self._connections[params.session_id] = (ctx.conn, sender)
        return {
            **result.model_dump(by_alias=True),
            "updates": [
                params.model_dump(by_alias=True)
                for method, params in sender.notifications
                if method == "session/update"
            ],
        }

    async def prompt(
        self,
        params: PromptRequest,
        *,
        client_peer: RuntimeClientPeer | None = None,
    ) -> dict[str, Any]:
        manager = self._manager()
        conn, sender = self._connection_for(params.session_id)
        sender.notifications.clear()
        sender.client_peer = client_peer
        try:
            result = await manager.prompt(
                HandlerContext(conn=conn, sender=sender, request_id=None),
                _to_contract_prompt(params),
            )
        finally:
            sender.client_peer = None
        return {
            "stopReason": result.stop_reason,
            "_meta": result.meta,
            "updates": []
            if client_peer is not None
            else [
                params.model_dump(by_alias=True)
                for method, params in sender.notifications
                if method == "session/update"
            ],
        }

    async def resume_session(self, params: ResumeSessionRequest) -> dict[str, Any]:
        manager = self._manager()
        sender = CollectingRuntimeSender()
        ctx = _handler_context(sender)
        result = await manager.resume_session(
            ctx,
            _to_contract_resume(params),
        )
        self._connections[params.session_id] = (ctx.conn, sender)
        return result.model_dump(by_alias=True)

    async def cancel(self, params: CancelNotification) -> None:
        manager = self._manager()
        conn, sender = self._connection_for(params.session_id)
        await manager.cancel(
            HandlerContext(conn=conn, sender=sender, request_id=None),
            _to_contract_cancel(params),
        )

    async def execute_shell(self, params: ExecuteShellRequest) -> dict[str, Any]:
        manager = self._manager()
        conn, sender = self._connection_for(params.session_id)
        sender.notifications.clear()
        result = await manager.execute_shell(
            HandlerContext(conn=conn, sender=sender, request_id=None),
            _to_contract_execute_shell(params),
        )
        return {
            "exitCode": result.exit_code,
            "cancelled": result.cancelled,
            "executionUpdates": [
                params.model_dump(by_alias=True)
                for method, params in sender.notifications
                if method == "_mustang.agent/session/execution_update"
            ],
        }

    async def execute_python(self, params: ExecutePythonRequest) -> dict[str, Any]:
        manager = self._manager()
        conn, sender = self._connection_for(params.session_id)
        sender.notifications.clear()
        result = await manager.execute_python(
            HandlerContext(conn=conn, sender=sender, request_id=None),
            _to_contract_execute_python(params),
        )
        return {
            "exitCode": result.exit_code,
            "cancelled": result.cancelled,
            "executionUpdates": [
                params.model_dump(by_alias=True)
                for method, params in sender.notifications
                if method == "_mustang.agent/session/execution_update"
            ],
        }

    async def cancel_execution(self, params: CancelExecutionRequest) -> None:
        manager = self._manager()
        conn, sender = self._connection_for(params.session_id)
        await manager.cancel_execution(
            HandlerContext(conn=conn, sender=sender, request_id=None),
            _to_contract_cancel_execution(params),
        )

    async def set_mode(self, params: SetSessionModeRequest) -> dict[str, Any]:
        manager = self._manager()
        conn, sender = self._connection_for(params.session_id)
        sender.notifications.clear()
        result = await manager.set_mode(
            HandlerContext(conn=conn, sender=sender, request_id=None),
            _to_contract_set_mode(params),
        )
        return {
            **result.model_dump(),
            "updates": [
                params.model_dump(by_alias=True)
                for method, params in sender.notifications
                if method == "session/update"
            ],
        }

    async def get_usage(self, params: GetUsageRequest) -> dict[str, Any]:
        manager = self._manager()
        conn, sender = self._connection_for(params.session_id)
        result = await manager.get_usage(
            HandlerContext(conn=conn, sender=sender, request_id=None),
            _to_contract_get_usage(params),
        )
        return result.model_dump(by_alias=True)

    async def close_session(self, params: CloseSessionRequest) -> dict[str, Any]:
        manager = self._manager()
        conn, sender = self._connection_for(params.session_id)
        result = await manager.close_session(
            HandlerContext(conn=conn, sender=sender, request_id=None),
            _to_contract_close(params),
        )
        self._connections.pop(params.session_id, None)
        return result.model_dump()

    def _manager(self) -> SessionManager:
        if self._session_manager is None:
            raise RuntimeError("session runtime service is not started")
        return self._session_manager

    def _connection_for(self, session_id: str) -> tuple[ConnectionContext, CollectingRuntimeSender]:
        entry = self._connections.get(session_id)
        if entry is not None:
            return entry
        sender = CollectingRuntimeSender()
        conn = _handler_context(sender).conn
        conn.bound_session_id = session_id
        self._connections[session_id] = (conn, sender)
        return conn, sender


async def _load(module_table: KernelModuleTable, name: str, factory: type[Subsystem]) -> None:
    instance = await factory.load(name, module_table)
    if instance is not None:
        module_table.register(instance)


def _prompt_user_dirs(workspace: Path) -> list[Path] | None:
    dirs = [
        Path.home() / ".mustang" / "prompts",
        workspace / ".mustang" / "prompts",
    ]
    existing = [path for path in dirs if path.is_dir()]
    return existing or None


def _handler_context(sender: CollectingRuntimeSender) -> HandlerContext:
    conn = ConnectionContext(
        auth=AuthContext(
            connection_id="agent-runtime",
            credential_type="token",
            remote_addr="agent-runtime",
            authenticated_at=datetime.now(timezone.utc),
        )
    )
    return HandlerContext(conn=conn, sender=sender, request_id=None)


def _to_contract_new(params: NewSessionRequest) -> Any:
    from kernel.protocol.interfaces.contracts.new_session_params import NewSessionParams

    return NewSessionParams.model_validate(params.model_dump())


def _to_contract_list(params: ListSessionsRequest) -> Any:
    from kernel.protocol.interfaces.contracts.list_sessions_params import ListSessionsParams

    return ListSessionsParams.model_validate(params.model_dump())


def _to_contract_load(params: LoadSessionRequest) -> Any:
    from kernel.protocol.interfaces.contracts.load_session_params import LoadSessionParams

    return LoadSessionParams.model_validate(params.model_dump())


def _to_contract_prompt(params: PromptRequest) -> Any:
    from kernel.protocol.interfaces.contracts.prompt_params import PromptParams

    return PromptParams.model_validate(params.model_dump())


def _to_contract_resume(params: ResumeSessionRequest) -> Any:
    from kernel.protocol.interfaces.contracts.resume_session_params import ResumeSessionParams

    return ResumeSessionParams.model_validate(params.model_dump())


def _to_contract_cancel(params: CancelNotification) -> Any:
    from kernel.protocol.interfaces.contracts.cancel_params import CancelParams

    return CancelParams.model_validate(params.model_dump())


def _to_contract_execute_shell(params: ExecuteShellRequest) -> Any:
    from kernel.protocol.interfaces.contracts.execute_shell_params import ExecuteShellParams

    return ExecuteShellParams.model_validate(params.model_dump())


def _to_contract_execute_python(params: ExecutePythonRequest) -> Any:
    from kernel.protocol.interfaces.contracts.execute_python_params import ExecutePythonParams

    return ExecutePythonParams.model_validate(params.model_dump())


def _to_contract_cancel_execution(params: CancelExecutionRequest) -> Any:
    from kernel.protocol.interfaces.contracts.cancel_execution_params import CancelExecutionParams

    return CancelExecutionParams.model_validate(params.model_dump())


def _to_contract_set_mode(params: SetSessionModeRequest) -> Any:
    from kernel.protocol.interfaces.contracts.set_mode_params import SetModeParams

    return SetModeParams.model_validate(params.model_dump())


def _to_contract_get_usage(params: GetUsageRequest) -> Any:
    from kernel.protocol.interfaces.contracts.get_usage_params import GetUsageParams

    return GetUsageParams.model_validate(params.model_dump())


def _to_contract_close(params: CloseSessionRequest) -> Any:
    from kernel.protocol.interfaces.contracts.close_session_params import CloseSessionParams

    return CloseSessionParams.model_validate(params.model_dump())


__all__ = ["AgentSessionRuntimeService", "CollectingRuntimeSender"]
