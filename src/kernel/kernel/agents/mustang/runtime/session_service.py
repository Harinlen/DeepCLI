"""No-FastAPI SessionManager host for a durable Agent Runtime."""

from __future__ import annotations

import logging
import os
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from kernel.agents.mustang.runtime.websocket_runtime import RuntimeClientPeer
from kernel.agents.mustang.commands import CommandManager
from kernel.core.config import ConfigManager
from kernel.agents.access.security import AuthContext
from kernel.core.flags import FlagManager, KernelFlags
from kernel.agents.mustang.gateways import GatewayManager
from kernel.agents.mustang.git import GitManager
from kernel.agents.mustang.hooks import HookManager
from kernel.agents.mustang.llm import LLMManager
from kernel.agents.mustang.llm_provider import LLMProviderManager
from kernel.agents.mustang.mcp import MCPManager
from kernel.agents.mustang.memory import MemoryManager
from kernel.agents.mustang.module_table import KernelModuleTable
from kernel.core.paths import user_path
from kernel.agents.mustang.prompts import PromptManager
from kernel.core.protocol.acp.schemas.session import (
    ActivateSkillRequest,
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
from kernel.core.protocol.interfaces.contracts.connection_context import ConnectionContext
from kernel.core.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.core.protocol.flags import ProtocolFlags
from kernel.agents.access.routes.flags import TransportFlags
from kernel.core.secrets import SecretManager
from kernel.agents.mustang.schedule import ScheduleManager
from kernel.agents.mustang.sessions import SessionManager
from kernel.agents.mustang.sessions.context import AgentContext
from kernel.agents.mustang.skills import SkillManager
from kernel.core.lifecycle import Subsystem
from kernel.agents.mustang.tool_authz import ToolAuthorizer
from kernel.agents.mustang.tools import ToolManager

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
    ("commands", CommandManager),
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
        if not isinstance(kernel_flags, KernelFlags):
            raise RuntimeError("kernel flags section did not return KernelFlags")

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
        await self._maybe_restart_self(sender)
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

    async def activate_skill(
        self,
        params: ActivateSkillRequest,
        *,
        client_peer: RuntimeClientPeer | None = None,
    ) -> dict[str, Any]:
        manager = self._manager()
        conn, sender = self._connection_for(params.session_id)
        sender.notifications.clear()
        sender.client_peer = client_peer
        try:
            result = await manager.activate_skill(
                HandlerContext(conn=conn, sender=sender, request_id=None),
                _to_contract_activate_skill(params),
            )
        finally:
            sender.client_peer = None
        await self._maybe_restart_self(sender)
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

    async def commands_list(self) -> dict[str, Any]:
        if self.module_table is None:
            raise RuntimeError("session runtime service is not started")
        commands = self.module_table.get(CommandManager)
        return {"commands": commands.list_command_dicts()}

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

    async def model_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run a model-management ACP request inside the Mustang Agent runtime."""
        from kernel.core.protocol.acp.routing import REQUEST_DISPATCH

        if self.module_table is None:
            raise RuntimeError("session runtime service is not started")
        spec = REQUEST_DISPATCH.get(method)
        if spec is None or spec.target != "model":
            raise ValueError(f"unsupported model request: {method}")

        from kernel.agents.mustang.llm import LLMManager

        model_handler = self.module_table.get(LLMManager)
        sender = CollectingRuntimeSender()
        ctx = _handler_context(sender)
        request_params = spec.params_type.model_validate(params)
        result = await spec.handler(model_handler, ctx, request_params)
        return result.model_dump(by_alias=True)

    def _manager(self) -> SessionManager:
        if self._session_manager is None:
            raise RuntimeError("session runtime service is not started")
        return self._session_manager

    async def _maybe_restart_self(self, sender: CollectingRuntimeSender) -> None:
        request = _restart_self_request(sender.notifications)
        if request is None:
            return
        socket_path = os.getenv("MUSTANG_SUPERVISOR_CONTROL_SOCKET", "")
        token = os.getenv("MUSTANG_SUPERVISOR_CONTROL_TOKEN", "")
        if not socket_path or not token:
            logger.warning("RestartSelf requested but Supervisor control is unavailable")
            return
        asyncio.create_task(
            _restart_self_after_ack(
                socket_path,
                token,
                str(request.get("agentId") or self.agent_id),
                str(request.get("reason") or "agent requested self-restart"),
            )
        )

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
        user_path("prompts"),
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
    from kernel.core.protocol.interfaces.contracts.new_session_params import NewSessionParams

    return NewSessionParams.model_validate(params.model_dump())


def _to_contract_list(params: ListSessionsRequest) -> Any:
    from kernel.core.protocol.interfaces.contracts.list_sessions_params import ListSessionsParams

    data = params.model_dump()
    meta = data.pop("meta") or {}
    data["include_empty"] = bool(meta.get("mustang.agent/includeEmpty"))
    return ListSessionsParams.model_validate(data)


def _to_contract_load(params: LoadSessionRequest) -> Any:
    from kernel.core.protocol.interfaces.contracts.load_session_params import LoadSessionParams

    return LoadSessionParams.model_validate(params.model_dump())


def _to_contract_prompt(params: PromptRequest) -> Any:
    from kernel.core.protocol.interfaces.contracts.prompt_params import PromptParams

    return PromptParams.model_validate(params.model_dump())


def _to_contract_activate_skill(params: ActivateSkillRequest) -> Any:
    from kernel.core.protocol.interfaces.contracts.activate_skill_params import ActivateSkillParams

    return ActivateSkillParams.model_validate(params.model_dump())


def _to_contract_resume(params: ResumeSessionRequest) -> Any:
    from kernel.core.protocol.interfaces.contracts.resume_session_params import ResumeSessionParams

    return ResumeSessionParams.model_validate(params.model_dump())


def _to_contract_cancel(params: CancelNotification) -> Any:
    from kernel.core.protocol.interfaces.contracts.cancel_params import CancelParams

    return CancelParams.model_validate(params.model_dump())


def _to_contract_execute_shell(params: ExecuteShellRequest) -> Any:
    from kernel.core.protocol.interfaces.contracts.execute_shell_params import ExecuteShellParams

    return ExecuteShellParams.model_validate(params.model_dump())


def _to_contract_execute_python(params: ExecutePythonRequest) -> Any:
    from kernel.core.protocol.interfaces.contracts.execute_python_params import ExecutePythonParams

    return ExecutePythonParams.model_validate(params.model_dump())


def _to_contract_cancel_execution(params: CancelExecutionRequest) -> Any:
    from kernel.core.protocol.interfaces.contracts.cancel_execution_params import CancelExecutionParams

    return CancelExecutionParams.model_validate(params.model_dump())


def _to_contract_set_mode(params: SetSessionModeRequest) -> Any:
    from kernel.core.protocol.interfaces.contracts.set_mode_params import SetModeParams

    return SetModeParams.model_validate(params.model_dump())


def _to_contract_get_usage(params: GetUsageRequest) -> Any:
    from kernel.core.protocol.interfaces.contracts.get_usage_params import GetUsageParams

    return GetUsageParams.model_validate(params.model_dump())


def _to_contract_close(params: CloseSessionRequest) -> Any:
    from kernel.core.protocol.interfaces.contracts.close_session_params import CloseSessionParams

    return CloseSessionParams.model_validate(params.model_dump())


def _restart_self_request(
    notifications: list[tuple[str, BaseModel]],
) -> dict[str, Any] | None:
    for _method, params in notifications:
        raw = params.model_dump(by_alias=True)
        meta = raw.get("meta") or raw.get("_meta")
        if not isinstance(meta, dict):
            continue
        request = meta.get("mustang.agent/restartSelf")
        if isinstance(request, dict):
            return request
    return None


async def request_control_async(
    socket_path: str,
    token: str,
    method: str,
    params: dict[str, object],
) -> dict[str, object]:
    from kernel.supervisor.control import request_control

    return await asyncio.to_thread(request_control, socket_path, token, method, params)


async def _restart_self_after_ack(
    socket_path: str,
    token: str,
    agent_id: str,
    reason: str,
) -> None:
    await asyncio.sleep(0.5)
    try:
        await request_control_async(
            socket_path,
            token,
            "restart_agent",
            {"agent_id": agent_id, "reason": reason},
        )
    except Exception:
        logger.exception("RestartSelf control request failed")


__all__ = ["AgentSessionRuntimeService", "CollectingRuntimeSender"]
