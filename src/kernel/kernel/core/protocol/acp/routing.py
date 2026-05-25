"""ACP method routing tables.

``REQUEST_DISPATCH`` and ``NOTIFICATION_DISPATCH`` use **ACP schema
types** (camelCase wire format) as ``params_type`` for validation.
Handler wrappers convert ACP types -> mustang contract types before
calling the appropriate handler, keeping both the session layer and
the LLM management layer free of ACP wire-format details.

Handler targets
---------------
Each ``RequestSpec`` carries a ``target`` field that names which
kernel handler the entry routes to:

- ``"session"`` -> ``SessionHandler`` (implemented by ``SessionManager``)
- ``"model"``   -> ``ModelHandler``   (implemented by ``LLMManager``)
- ``"secrets"`` -> ``SecretManager``  (bootstrap service on module table)
- ``"commands"`` -> ``CommandManager`` (slash command catalog)
- ``"skills"`` -> ``SkillManager`` (skill management catalog)

``AcpSessionHandler._route_request`` reads ``target`` to select the
right handler object from ``KernelModuleTable``.  Adding a new target
is a two-step change: add the ``Literal`` value here and add the
matching ``_get_<target>_handler()`` branch in ``session_handler.py``.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel
from pydantic.alias_generators import to_camel
import orjson

from kernel.agents.mustang.llm.config import ModelRef
from kernel.core.protocol.acp.namespaces import AcpMethod, MustangMethod
from kernel.core.protocol.acp.schemas.commands import (
    CommandEntry,
    ListCommandsRequest,
    ListCommandsResponse,
)
from kernel.core.protocol.acp.schemas.cron import (
    CronCreateRequest,
    CronCreateResponse,
    CronDeleteRequest,
    CronDeleteResponse,
    CronListRequest,
    CronListResponse,
)
from kernel.core.protocol.acp.schemas.skills import (
    SkillCommandEntry,
    SkillInspectEntry,
    SkillRecordEntry,
    SkillsInspectRequest,
    SkillsInspectResponse,
    SkillsListRequest,
    SkillsListResponse,
    SkillsRefreshRequest,
    SkillsRefreshResponse,
)
from kernel.core.protocol.acp.schemas.agents import (
    AgentHealthRequest,
    AgentHealthResponse,
    AgentLifecycleRequest,
    AgentLifecycleResponse,
    AgentRecordResponse,
    AgentSendRequest,
    AgentSendResponse,
    AgentsAddRequest,
    AgentsBindRequest,
    AgentsBindResponse,
    AgentsBindingsRequest,
    AgentsBindingsResponse,
    AgentsDeleteRequest,
    AgentsDeleteResponse,
    AgentsGrantRequest,
    AgentsGrantResponse,
    AgentsGrantsRequest,
    AgentsGrantsResponse,
    AgentsListRequest,
    AgentsListResponse,
    AgentsRevokeGrantRequest,
    AgentsSetIdentityRequest,
    AgentsUnbindRequest,
    AgentsUnbindResponse,
)
from kernel.core.protocol.acp.schemas.flags import (
    FlagSectionEntry,
    FlagsListRequest,
    FlagsListResponse,
    FlagsReadRequest,
    FlagsReadResponse,
    FlagsResetRequest,
    FlagsSetRequest,
    FlagsWriteResponse,
)
from kernel.core.protocol.acp.schemas.global_resource import (
    GlobalBackupRequest,
    GlobalBackupResponse,
    GlobalBackupsRequest,
    GlobalBackupsResponse,
    GlobalExportRequest,
    GlobalExportResponse,
    GlobalImportRequest,
    GlobalImportResponse,
    GlobalRestoreRequest,
    GlobalRestoreResponse,
)
from kernel.core.protocol.acp.schemas.gateways import (
    GatewayBindRequest,
    GatewayBindResponse,
    GatewayBindingsRequest,
    GatewayBindingsResponse,
    GatewayCreateRequest,
    GatewayDeleteRequest,
    GatewayDeleteResponse,
    GatewayIdRequest,
    GatewayReloadRequest,
    GatewayReloadResponse,
    GatewayRecordResponse,
    GatewayRevisionResponse,
    GatewayUnbindRequest,
    GatewayUnbindResponse,
    GatewaysListRequest,
    GatewaysListResponse,
    GatewaysStatusRequest,
    GatewaysStatusResponse,
)
from kernel.core.protocol.acp.schemas.mcp import (
    MCPDeleteRequest,
    MCPDeleteResponse,
    MCPListRequest,
    MCPListResponse,
    MCPReadRequest,
    MCPReadResponse,
    MCPWriteRequest,
    MCPWriteResponse,
)
from kernel.core.protocol.acp.schemas.memory import (
    MemoryDeleteRequest,
    MemoryDeleteResponse,
    MemoryListRequest,
    MemoryListResponse,
    MemoryShowRequest,
    MemoryShowResponse,
)
from kernel.core.protocol.acp.schemas.model import (
    AcpProfileEntry,
    AcpProviderEntry,
    AcpProviderTypeEntry,
    AddModelRequest,
    AddProviderRequest,
    AddProviderResponse,
    ListProfilesRequest,
    ListProfilesResponse,
    ListProvidersRequest,
    ListProvidersResponse,
    RefreshModelsRequest,
    RefreshModelsResponse,
    RemoveProviderRequest,
    RemoveProviderResponse,
    SetCurrentModelRequest,
    SetCurrentModelResponse,
    UpdateModelRequest,
    UpdateModelResponse,
)
from kernel.core.protocol.acp.schemas.session import (
    AcpSessionInfo,
    ActivateSkillRequest,
    ActivateSkillResponse,
    ArchiveSessionRequest,
    ArchiveSessionResponse,
    CancelExecutionRequest,
    CancelExecutionResponse,
    CancelNotification,
    CloseSessionRequest,
    CloseSessionResponse,
    DeleteSessionRequest,
    DeleteSessionResponse,
    GetUsageRequest,
    GetUsageResponse,
    ToolSnapshotRequest,
    ToolSnapshotResponse,
    ExecutePythonRequest,
    ExecutePythonResponse,
    ExecuteShellRequest,
    ExecuteShellResponse,
    ListSessionsRequest,
    ListSessionsResponse,
    LoadSessionRequest,
    LoadSessionResponse,
    NewSessionRequest,
    NewSessionResponse,
    PromptRequest,
    PromptResponse,
    RenameSessionRequest,
    RenameSessionResponse,
    ResumeSessionRequest,
    ResumeSessionResponse,
    SetSessionConfigOptionRequest,
    SetSessionConfigOptionResponse,
    SetSessionModeRequest,
    SetSessionModeResponse,
)
from kernel.core.protocol.acp.schemas.secrets import (
    SecretAuditEntry,
    SecretMetaEntry,
    SecretsAuditRequest,
    SecretsAuditResponse,
    SecretsDeleteRequest,
    SecretsDeleteResponse,
    SecretsListRequest,
    SecretsListResponse,
    SecretsRenameRequest,
    SecretsRenameResponse,
)
from kernel.core.protocol.acp.schemas.web_fetch import (
    SetWebFetchBackendRequest,
    SetWebFetchBackendResponse,
    SetWebFetchConfigRequest,
    SetWebFetchConfigResponse,
    WebBridgePairResetRequest,
    WebBridgePairStartRequest,
    WebBridgeStatusRequest,
    WebBridgeStatusResponse,
    WebFetchBackendOptionsRequest,
    WebFetchBackendOptionsResponse,
    WebFetchConfigRequest,
    WebFetchConfigResponse,
)
from kernel.core.protocol.interfaces.contracts.archive_session_params import ArchiveSessionParams
from kernel.core.protocol.interfaces.contracts.activate_skill_params import ActivateSkillParams
from kernel.core.protocol.interfaces.contracts.archive_session_result import ArchiveSessionResult
from kernel.core.protocol.interfaces.contracts.close_session_params import CloseSessionParams
from kernel.core.protocol.interfaces.contracts.close_session_result import CloseSessionResult
from kernel.core.protocol.interfaces.contracts.delete_session_params import DeleteSessionParams
from kernel.core.protocol.interfaces.contracts.delete_session_result import DeleteSessionResult
from kernel.core.protocol.interfaces.contracts.cancel_execution_params import (
    CancelExecutionParams,
)
from kernel.core.protocol.interfaces.contracts.add_provider_params import (
    AddProviderParams,
)
from kernel.core.protocol.interfaces.contracts.add_model_params import AddModelParams
from kernel.core.protocol.interfaces.contracts.cancel_params import CancelParams
from kernel.core.protocol.interfaces.contracts.execute_python_params import (
    ExecutePythonParams,
)
from kernel.core.protocol.interfaces.contracts.execute_shell_params import ExecuteShellParams
from kernel.core.protocol.interfaces.contracts.get_usage_params import GetUsageParams
from kernel.core.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.core.protocol.interfaces.errors import InvalidParams
from kernel.core.storage.global_commands import GlobalRestoreUnavailable
from kernel.core.protocol.interfaces.contracts.list_profiles_params import (
    ListProfilesParams,
)
from kernel.core.protocol.interfaces.contracts.list_providers_params import (
    ListProvidersParams,
)
from kernel.core.protocol.interfaces.contracts.list_sessions_params import (
    ListSessionsParams,
)
from kernel.core.protocol.interfaces.contracts.list_sessions_result import (
    ListSessionsResult,
)
from kernel.core.protocol.interfaces.contracts.load_session_params import (
    LoadSessionParams,
)
from kernel.core.protocol.interfaces.contracts.load_session_result import (
    LoadSessionResult,
)
from kernel.core.protocol.interfaces.contracts.new_session_params import (
    NewSessionParams,
)
from kernel.core.protocol.interfaces.contracts.new_session_result import (
    NewSessionResult,
)
from kernel.core.protocol.interfaces.contracts.prompt_params import PromptParams
from kernel.core.protocol.interfaces.contracts.prompt_result import PromptResult
from kernel.core.protocol.interfaces.contracts.rename_session_params import RenameSessionParams
from kernel.core.protocol.interfaces.contracts.rename_session_result import RenameSessionResult
from kernel.core.protocol.interfaces.contracts.resume_session_params import ResumeSessionParams
from kernel.core.protocol.interfaces.contracts.resume_session_result import ResumeSessionResult
from kernel.core.protocol.interfaces.contracts.refresh_models_params import (
    RefreshModelsParams,
)
from kernel.core.protocol.interfaces.contracts.remove_provider_params import (
    RemoveProviderParams,
)
from kernel.core.protocol.interfaces.contracts.set_config_option_params import (
    SetConfigOptionParams,
)
from kernel.core.protocol.interfaces.contracts.set_config_option_result import (
    SetConfigOptionResult,
)
from kernel.core.protocol.interfaces.contracts.set_current_model_params import (
    SetCurrentModelParams,
)
from kernel.core.protocol.interfaces.contracts.update_model_params import UpdateModelParams
from kernel.core.protocol.interfaces.contracts.update_model_result import UpdateModelResult
from kernel.core.protocol.interfaces.contracts.set_mode_params import SetModeParams
from kernel.core.protocol.interfaces.contracts.set_mode_result import SetModeResult
from kernel.core.protocol.acp.schemas.auth import AuthRequest, AuthResult
from kernel.core.protocol.interfaces.model_handler import ModelHandler
from kernel.core.protocol.interfaces.session_handler import SessionHandler

# Discriminator for which kernel subsystem handles a request.
HandlerTarget = Literal[
    "session",
    "model",
    "secrets",
    "commands",
    "skills",
    "tools",
    "global",
    "flags",
    "agents",
    "gateways",
    "mcp",
    "schedule",
    "memory",
]


@dataclass(frozen=True)
class RequestSpec:
    handler: Callable[[Any, HandlerContext, Any], Awaitable[BaseModel]]
    """Handler wrapper function.  First arg is the target handler object."""

    params_type: type[BaseModel]
    """ACP wire-format schema type used for validation."""

    result_type: type[BaseModel]

    target: HandlerTarget = field(default="session")
    """Which kernel handler to route this request to."""


@dataclass(frozen=True)
class NotificationSpec:
    handler: Callable[[SessionHandler, HandlerContext, Any], Awaitable[None]]
    params_type: type[BaseModel]
    """ACP wire-format schema type used for validation."""


# ---------------------------------------------------------------------------
# session/* handler wrappers
# ---------------------------------------------------------------------------


def _camelise(value: Any) -> Any:
    if isinstance(value, dict):
        return {to_camel(k): _camelise(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_camelise(item) for item in value]
    return value


def _dump_contract(value: Any) -> dict[str, Any]:
    return _camelise(value.model_dump(by_alias=False, exclude_none=True))


def _dump_contract_list(values: list[Any]) -> list[dict[str, Any]]:
    return [_dump_contract(value) for value in values]


def _session_info(s: Any) -> AcpSessionInfo:
    meta = dict(s.meta or {})
    session_meta = dict(meta.get("mustang.agent/session") or {})
    if s.archived_at is not None:
        session_meta["archivedAt"] = s.archived_at
    if s.title_source is not None:
        session_meta["titleSource"] = s.title_source
    if session_meta:
        meta["mustang.agent/session"] = session_meta
    return AcpSessionInfo(
        session_id=s.session_id,
        cwd=s.cwd,
        updated_at=s.updated_at,
        title=s.title,
        message_count=getattr(s, "message_count", 0),
        turn_count=getattr(s, "turn_count", 0),
        meta=meta or None,
    )


async def _handle_new(sh: SessionHandler, ctx: HandlerContext, p: NewSessionRequest) -> BaseModel:
    result = await sh.new(
        ctx,
        NewSessionParams(
            cwd=p.cwd,
            mcp_servers=[s.model_dump() for s in p.mcp_servers],
            meta=p.meta,
        ),
    )
    return NewSessionResponse(
        session_id=result.session_id,
        config_options=_dump_contract_list(result.config_options),
        modes=_dump_contract(result.modes) if result.modes is not None else None,
    )


async def _handle_load(sh: SessionHandler, ctx: HandlerContext, p: LoadSessionRequest) -> BaseModel:
    result = await sh.load_session(
        ctx,
        LoadSessionParams(
            session_id=p.session_id,
            cwd=p.cwd,
            mcp_servers=[s.model_dump() for s in p.mcp_servers],
        ),
    )
    return LoadSessionResponse(
        config_options=_dump_contract_list(result.config_options),
        modes=_dump_contract(result.modes) if result.modes is not None else None,
    )


async def _handle_resume(
    sh: SessionHandler, ctx: HandlerContext, p: ResumeSessionRequest
) -> BaseModel:
    result = await sh.resume_session(
        ctx,
        ResumeSessionParams(
            session_id=p.session_id,
            cwd=p.cwd,
        ),
    )
    return ResumeSessionResponse(
        config_options=_dump_contract_list(result.config_options),
        modes=_dump_contract(result.modes) if result.modes is not None else None,
    )


async def _handle_close(
    sh: SessionHandler, ctx: HandlerContext, p: CloseSessionRequest
) -> BaseModel:
    await sh.close_session(ctx, CloseSessionParams(session_id=p.session_id))
    return CloseSessionResponse()


async def _handle_list(
    sh: SessionHandler, ctx: HandlerContext, p: ListSessionsRequest
) -> BaseModel:
    result = await sh.list(
        ctx,
        ListSessionsParams(
            cursor=p.cursor,
            cwd=p.cwd,
            include_archived=_session_filter(p, "includeArchived", p.include_archived),
            archived_only=_session_filter(p, "archivedOnly", p.archived_only),
        ),
    )
    return ListSessionsResponse(
        sessions=[_session_info(s) for s in result.sessions],
        next_cursor=result.next_cursor,
    )


async def _handle_prompt(sh: SessionHandler, ctx: HandlerContext, p: PromptRequest) -> BaseModel:
    from kernel.core.protocol.interfaces.contracts.text_block import TextBlock
    from kernel.core.protocol.interfaces.contracts.image_block import ImageBlock
    from kernel.core.protocol.interfaces.contracts.resource_block import ResourceBlock
    from kernel.core.protocol.interfaces.contracts.resource_link_block import ResourceLinkBlock

    _type_map = {
        "text": TextBlock,
        "image": ImageBlock,
        "resource": ResourceBlock,
        "resource_link": ResourceLinkBlock,
    }

    blocks = []
    for b in p.prompt:
        block_type = _type_map.get(b.type)  # type: ignore[union-attr]
        if block_type is not None:
            blocks.append(block_type.model_validate(b.model_dump(by_alias=False)))  # type: ignore[attr-defined]

    result = await sh.prompt(
        ctx,
        PromptParams(
            session_id=p.session_id,
            prompt=blocks,
            max_turns=_max_turns(p),
            meta=p.meta,
        ),
    )
    meta = result.meta if isinstance(result.meta, dict) else None
    return PromptResponse(stop_reason=result.stop_reason, meta=meta)


async def _handle_activate_skill(
    sh: SessionHandler, ctx: HandlerContext, p: ActivateSkillRequest
) -> BaseModel:
    result = await sh.activate_skill(
        ctx,
        ActivateSkillParams(
            session_id=p.session_id,
            skill=p.skill,
            args=p.args,
            meta=p.meta,
        ),
    )
    meta = result.meta if isinstance(result.meta, dict) else None
    return ActivateSkillResponse(stop_reason=result.stop_reason, meta=meta)


def _max_turns(p: PromptRequest) -> int:
    value = (p.meta or {}).get("mustang.agent/maxTurns", p.max_turns)
    try:
        return int(value)
    except (TypeError, ValueError):
        return p.max_turns


def _session_filter(p: ListSessionsRequest, name: str, fallback: bool) -> bool:
    filters = (p.meta or {}).get("mustang.agent/sessionFilters")
    if isinstance(filters, dict) and name in filters:
        return bool(filters[name])
    return fallback


async def _handle_execute_shell(
    sh: SessionHandler, ctx: HandlerContext, p: ExecuteShellRequest
) -> BaseModel:
    result = await sh.execute_shell(
        ctx,
        ExecuteShellParams(
            session_id=p.session_id,
            command=p.command,
            exclude_from_context=p.exclude_from_context,
            shell=p.shell,  # type: ignore[arg-type]
        ),
    )
    return ExecuteShellResponse(exit_code=result.exit_code, cancelled=result.cancelled)


async def _handle_execute_python(
    sh: SessionHandler, ctx: HandlerContext, p: ExecutePythonRequest
) -> BaseModel:
    result = await sh.execute_python(
        ctx,
        ExecutePythonParams(
            session_id=p.session_id,
            code=p.code,
            exclude_from_context=p.exclude_from_context,
        ),
    )
    return ExecutePythonResponse(exit_code=result.exit_code, cancelled=result.cancelled)


async def _handle_cancel_execution(
    sh: SessionHandler, ctx: HandlerContext, p: CancelExecutionRequest
) -> BaseModel:
    await sh.cancel_execution(
        ctx,
        CancelExecutionParams(session_id=p.session_id, kind=p.kind),  # type: ignore[arg-type]
    )
    return CancelExecutionResponse()


async def _notify_cancel_execution(
    sh: SessionHandler, ctx: HandlerContext, p: CancelExecutionRequest
) -> None:
    await sh.cancel_execution(
        ctx,
        CancelExecutionParams(session_id=p.session_id, kind=p.kind),  # type: ignore[arg-type]
    )


async def _handle_set_mode(
    sh: SessionHandler, ctx: HandlerContext, p: SetSessionModeRequest
) -> BaseModel:
    await sh.set_mode(ctx, SetModeParams(session_id=p.session_id, mode_id=p.mode_id))
    return SetSessionModeResponse()


async def _handle_set_config_option(
    sh: SessionHandler, ctx: HandlerContext, p: SetSessionConfigOptionRequest
) -> BaseModel:
    result = await sh.set_config_option(
        ctx,
        SetConfigOptionParams(
            session_id=p.session_id,
            config_id=p.config_id,
            value=p.value,
        ),
    )
    return SetSessionConfigOptionResponse(config_options=_dump_contract_list(result.config_options))


async def _handle_rename_session(
    sh: SessionHandler, ctx: HandlerContext, p: RenameSessionRequest
) -> BaseModel:
    result = await sh.rename_session(
        ctx,
        RenameSessionParams(session_id=p.session_id, title=p.title),
    )
    return RenameSessionResponse(session=_session_info(result))


async def _handle_archive_session(
    sh: SessionHandler, ctx: HandlerContext, p: ArchiveSessionRequest
) -> BaseModel:
    result = await sh.archive_session(
        ctx,
        ArchiveSessionParams(session_id=p.session_id, archived=p.archived),
    )
    return ArchiveSessionResponse(session=_session_info(result))


async def _handle_delete_session(
    sh: SessionHandler, ctx: HandlerContext, p: DeleteSessionRequest
) -> BaseModel:
    result = await sh.delete_session(
        ctx,
        DeleteSessionParams(session_id=p.session_id, force=p.force),
    )
    return DeleteSessionResponse(deleted=result.deleted)


async def _handle_get_usage(
    sh: SessionHandler, ctx: HandlerContext, p: GetUsageRequest
) -> BaseModel:
    result = await sh.get_usage(ctx, GetUsageParams(session_id=p.session_id))
    return GetUsageResponse.model_validate(_dump_contract(result))


async def _handle_tool_snapshot(
    sh: SessionHandler, ctx: HandlerContext, p: ToolSnapshotRequest
) -> BaseModel:
    result = await sh.tool_snapshot(ctx, p.session_id)
    return ToolSnapshotResponse.model_validate(result)


async def _handle_cancel(sh: SessionHandler, ctx: HandlerContext, p: CancelNotification) -> None:
    await sh.cancel(ctx, CancelParams(session_id=p.session_id))


# ---------------------------------------------------------------------------
# model/* handler wrappers
# ---------------------------------------------------------------------------


async def _handle_profile_list(
    mh: ModelHandler, ctx: HandlerContext, p: ListProfilesRequest
) -> BaseModel:
    result = await mh.list_profiles(ctx, ListProfilesParams())
    return ListProfilesResponse(
        profiles=[
            AcpProfileEntry(
                name=info.name,
                provider_type=info.provider_type,
                model_id=info.model_id,
                context_window=info.context_window,
                is_default=info.is_default,
            )
            for info in result.profiles
        ],
        default_model=result.default_model,
    )


async def _handle_provider_list(
    mh: ModelHandler, ctx: HandlerContext, p: ListProvidersRequest
) -> BaseModel:
    result = await mh.list_providers(ctx, ListProvidersParams())
    return ListProvidersResponse(
        providers=[
            AcpProviderEntry(
                name=info.name,
                provider_type=info.provider_type,
                base_url=info.base_url,
                effective_base_url=info.effective_base_url,
                aws_region=info.aws_region,
                has_api_key=info.has_api_key,
                api_key_display=info.api_key_display,
                has_aws_secret_key=info.has_aws_secret_key,
                aws_secret_key_display=info.aws_secret_key_display,
                setting_fields=info.setting_fields,
                models=info.models,
                context_windows=info.context_windows,
                display_names=info.display_names,
                roles=info.roles,
            )
            for info in result.providers
        ],
        provider_type_options=[
            AcpProviderTypeEntry(
                provider_type=info.provider_type,
                setting_fields=info.setting_fields,
                effective_base_url=info.effective_base_url,
            )
            for info in result.provider_type_options
        ],
        current_used=result.current_used,
        default_context_window=result.default_context_window,
    )


async def _handle_provider_add(
    mh: ModelHandler, ctx: HandlerContext, p: AddProviderRequest
) -> BaseModel:
    result = await mh.add_provider(
        ctx,
        AddProviderParams(
            name=p.name,
            provider_type=p.provider_type,
            api_key=p.api_key,
            base_url=p.base_url,
            aws_secret_key=p.aws_secret_key,
            aws_region=p.aws_region,
            models=p.models,
        ),
    )
    return AddProviderResponse(name=result.name, models=result.models)


async def _handle_provider_remove(
    mh: ModelHandler, ctx: HandlerContext, p: RemoveProviderRequest
) -> BaseModel:
    await mh.remove_provider(ctx, RemoveProviderParams(name=p.name))
    return RemoveProviderResponse()


async def _handle_provider_refresh(
    mh: ModelHandler, ctx: HandlerContext, p: RefreshModelsRequest
) -> BaseModel:
    result = await mh.refresh_models(ctx, RefreshModelsParams(name=p.name))
    return RefreshModelsResponse(models=result.models)


async def _handle_set_current(
    mh: ModelHandler, ctx: HandlerContext, p: SetCurrentModelRequest
) -> BaseModel:
    result = await mh.set_current_model(
        ctx,
        SetCurrentModelParams(
            role=p.role,
            model=ModelRef(provider=p.provider, model=p.model),
        ),
    )
    return SetCurrentModelResponse(role=result.role, model=result.model)


async def _handle_model_add(mh: ModelHandler, ctx: HandlerContext, p: AddModelRequest) -> BaseModel:
    result = await mh.add_model(
        ctx,
        AddModelParams(
            provider_name=p.provider_name,
            provider_type=p.provider_type,
            api_key=p.api_key,
            base_url=p.base_url,
            aws_secret_key=p.aws_secret_key,
            aws_region=p.aws_region,
            model_id=p.model_id,
            display_name=p.display_name,
            context_window=p.context_window,
            roles=p.roles,
        ),
    )
    return _model_update_response(result)


async def _handle_model_update(
    mh: ModelHandler, ctx: HandlerContext, p: UpdateModelRequest
) -> BaseModel:
    result = await mh.update_model(
        ctx,
        UpdateModelParams(
            model=ModelRef(provider=p.provider, model=p.model),
            provider_name=p.provider_name,
            provider_type=p.provider_type,
            api_key=p.api_key,
            base_url=p.base_url,
            aws_secret_key=p.aws_secret_key,
            aws_region=p.aws_region,
            model_id=p.model_id,
            display_name=p.display_name,
            context_window=p.context_window,
            roles=p.roles,
        ),
    )
    return _model_update_response(result)


def _model_update_response(result: UpdateModelResult) -> UpdateModelResponse:
    return UpdateModelResponse(
        model=result.model,
        provider_type=result.provider_type,
        base_url=result.base_url,
        effective_base_url=result.effective_base_url,
        aws_region=result.aws_region,
        has_api_key=result.has_api_key,
        api_key_display=result.api_key_display,
        has_aws_secret_key=result.has_aws_secret_key,
        aws_secret_key_display=result.aws_secret_key_display,
        setting_fields=result.setting_fields,
        display_name=result.display_name,
        context_window=result.context_window,
        roles=result.roles,
    )


# ---------------------------------------------------------------------------
# secrets/* handler wrappers
# ---------------------------------------------------------------------------


async def _handle_auth(
    sm: Any,
    ctx: HandlerContext,
    p: Any,
) -> BaseModel:
    """Route ``secrets/auth`` actions to :class:`SecretManager`."""
    from kernel.core.protocol.interfaces.errors import InvalidParams
    from kernel.core.secrets.types import SecretNotFoundError
    import os

    action = p.action
    if action == "set":
        if not p.name or p.value is None:
            raise InvalidParams("'name' and 'value' are required for action 'set'")
        sm.set(p.name, p.value, kind=p.kind or "static")
        return AuthResult()
    if action == "get":
        if not p.name:
            raise InvalidParams("'name' is required for action 'get'")
        val = sm.get(p.name)
        return AuthResult(value=_mask_secret(val))
    if action == "list":
        return AuthResult(names=sm.list_names(kind=p.kind))
    if action == "delete":
        if not p.name:
            raise InvalidParams("'name' is required for action 'delete'")
        sm.delete(p.name)
        return AuthResult()
    if action == "import_env":
        if not p.env_var or not p.name:
            raise InvalidParams("'env_var' and 'name' are required for action 'import_env'")
        val = os.environ.get(p.env_var)
        if val is None:
            raise SecretNotFoundError(f"env var {p.env_var!r} not set")
        sm.set(p.name, val)
        return AuthResult()
    raise InvalidParams(f"Unknown auth action: {action!r}")


def _mask_secret(value: str | None) -> str | None:
    """Mask a secret value for display: show last 4 chars only."""
    if value is None:
        return None
    if len(value) <= 4:
        return "****"
    return "****" + value[-4:]


# ---------------------------------------------------------------------------
# global/* handler wrappers
# ---------------------------------------------------------------------------


def _require_primary(actor_agent_id: str) -> None:
    if actor_agent_id != "primary":
        raise InvalidParams("only primary Agent may run this management method")


async def _handle_global_backup(
    service: Any, ctx: HandlerContext, p: GlobalBackupRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    record = service.backup(
        actor_agent_id=p.actor_agent_id,
        output_dir=Path(p.output_dir) if p.output_dir else None,
    )
    return GlobalBackupResponse(
        path=record.path,
        checksum=record.checksum,
        source_schema_version=record.source_schema_version,
    )


async def _handle_global_backups(
    service: Any, ctx: HandlerContext, p: GlobalBackupsRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    result = service.backups(
        actor_agent_id=p.actor_agent_id,
        backup_dir=Path(p.backup_dir) if p.backup_dir else None,
    )
    return GlobalBackupsResponse(backups=list(result.backups))


async def _handle_global_export(
    service: Any, ctx: HandlerContext, p: GlobalExportRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    result = service.export(
        actor_agent_id=p.actor_agent_id,
        output_path=Path(p.output_path) if p.output_path else None,
        dry_run=p.dry_run,
        include_history=p.include_history,
    )
    return GlobalExportResponse(
        dry_run=result.dry_run,
        format="json",
        output_path=result.output_path,
        resource_count=result.resource_count,
        event_count=result.event_count,
        warnings=list(result.warnings),
    )


async def _handle_global_import(
    service: Any, ctx: HandlerContext, p: GlobalImportRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        if p.dry_run:
            result = service.import_dry_run(
                actor_agent_id=p.actor_agent_id,
                input_path=Path(p.input_path),
            )
            meta = None
        else:
            result = service.import_apply(
                actor_agent_id=p.actor_agent_id,
                input_path=Path(p.input_path),
            )
            meta = None
    except GlobalRestoreUnavailable as exc:
        from kernel.core.storage.models import ImportReport

        result = ImportReport(
            dry_run=False,
            planned_writes=0,
            conflicts=(),
            errors=(),
            warnings=(str(exc),),
        )
        meta = {"unavailable": True}
    return GlobalImportResponse(
        dry_run=result.dry_run,
        planned_writes=result.planned_writes,
        conflicts=list(result.conflicts),
        errors=list(result.errors),
        warnings=list(result.warnings),
        unavailable=meta is not None and bool(meta.get("unavailable")),
        meta=meta,
    )


async def _handle_global_restore(
    service: Any, ctx: HandlerContext, p: GlobalRestoreRequest
) -> BaseModel:
    del service, ctx
    _require_primary(p.actor_agent_id)
    return GlobalRestoreResponse(
        message="online global restore is unavailable until global writes can be quiesced"
    )


# ---------------------------------------------------------------------------
# flags/* handler wrappers
# ---------------------------------------------------------------------------


async def _handle_flags_list(fm: Any, ctx: HandlerContext, p: FlagsListRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    return FlagsListResponse(
        sections=[FlagSectionEntry.model_validate(item) for item in fm.management_list()]
    )


async def _handle_flags_read(fm: Any, ctx: HandlerContext, p: FlagsReadRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    return FlagsReadResponse.model_validate(fm.management_read(p.section))


async def _handle_flags_set(fm: Any, ctx: HandlerContext, p: FlagsSetRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        result = fm.management_set(
            section=p.section,
            key=p.key,
            value=p.value,
            expected_revision=p.expected_revision,
            actor=p.actor_agent_id,
        )
    except (KeyError, ValueError) as exc:
        raise InvalidParams(str(exc))
    return FlagsWriteResponse.model_validate(result)


async def _handle_flags_reset(fm: Any, ctx: HandlerContext, p: FlagsResetRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        result = fm.management_reset(
            section=p.section,
            key=p.key,
            expected_revision=p.expected_revision,
            actor=p.actor_agent_id,
        )
    except (KeyError, ValueError) as exc:
        raise InvalidParams(str(exc))
    return FlagsWriteResponse.model_validate(result)


# ---------------------------------------------------------------------------
# secrets metadata handler wrappers
# ---------------------------------------------------------------------------


async def _handle_secrets_list(sm: Any, ctx: HandlerContext, p: SecretsListRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    return SecretsListResponse(
        secrets=[
            SecretMetaEntry(
                secret_id=record.secret_id,
                name=record.name,
                revision=record.revision,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            for record in sm.list()
        ]
    )


async def _handle_secrets_audit(sm: Any, ctx: HandlerContext, p: SecretsAuditRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    events = []
    for event in sm.audit(p.secret_id):
        metadata = orjson.loads(event.metadata_json or "{}")
        events.append(
            SecretAuditEntry(
                id=event.id,
                secret_id=event.secret_id,
                event_type=event.event_type,
                actor_agent_id=event.actor_agent_id,
                created_at=event.created_at,
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        )
    return SecretsAuditResponse(events=events)


async def _handle_secrets_rename(
    sm: Any, ctx: HandlerContext, p: SecretsRenameRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    ref = sm.rename(
        p.secret_id,
        p.name,
        expected_revision=p.expected_revision,
        actor=p.actor_agent_id,
    )
    return SecretsRenameResponse(
        secret_id=ref.secret_id,
        ref=ref.ref,
        name=ref.name,
        revision=ref.revision,
    )


async def _handle_secrets_delete(
    sm: Any, ctx: HandlerContext, p: SecretsDeleteRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        deleted = sm.delete_uuid(
            p.secret_id,
            expected_revision=p.expected_revision,
            actor=p.actor_agent_id,
            confirm=p.confirm,
        )
    except ValueError as exc:
        raise InvalidParams(str(exc))
    return SecretsDeleteResponse(deleted=deleted)


# ---------------------------------------------------------------------------
# agents/* and agent/send handler wrappers
# ---------------------------------------------------------------------------


def _invalid_management_error(exc: Exception) -> InvalidParams:
    return InvalidParams(str(exc))


async def _handle_agents_list(service: Any, ctx: HandlerContext, p: AgentsListRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    return AgentsListResponse.model_validate(
        _camelise(service.list(include_bindings=p.include_bindings))
    )


async def _handle_agents_add(service: Any, ctx: HandlerContext, p: AgentsAddRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        agent = service.add(
            p.agent_id,
            workspace=Path(p.workspace),
            name=p.name,
            state_dir=Path(p.state_dir) if p.state_dir else None,
            actor_agent_id=p.actor_agent_id,
        )
    except (KeyError, ValueError, PermissionError, RuntimeError) as exc:
        raise _invalid_management_error(exc)
    return AgentRecordResponse(agent=_camelise(agent))


async def _handle_agents_delete(
    service: Any, ctx: HandlerContext, p: AgentsDeleteRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        result = service.delete(
            p.agent_id,
            confirm=p.confirm,
            actor_agent_id=p.actor_agent_id,
        )
    except (KeyError, ValueError, PermissionError, RuntimeError) as exc:
        raise _invalid_management_error(exc)
    return AgentsDeleteResponse.model_validate(result)


async def _handle_agents_set_identity(
    service: Any, ctx: HandlerContext, p: AgentsSetIdentityRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        agent = service.set_identity(
            p.agent_id,
            name=p.name,
            avatar=p.avatar,
            theme=p.theme,
            identity_patch=p.identity_patch,
            actor_agent_id=p.actor_agent_id,
        )
    except (KeyError, ValueError, PermissionError, RuntimeError) as exc:
        raise _invalid_management_error(exc)
    return AgentRecordResponse(agent=_camelise(agent))


async def _handle_agents_bindings(
    service: Any, ctx: HandlerContext, p: AgentsBindingsRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    return AgentsBindingsResponse(bindings=_camelise(service.bindings(agent_id=p.agent_id)))


async def _handle_agents_bind(service: Any, ctx: HandlerContext, p: AgentsBindRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        binding = service.bind(
            agent_id=p.agent_id,
            bind=p.bind,
            session_id=p.session_id,
            actor_agent_id=p.actor_agent_id,
        )
    except (KeyError, ValueError, PermissionError, RuntimeError) as exc:
        raise _invalid_management_error(exc)
    return AgentsBindResponse(binding=_camelise(binding))


async def _handle_agents_unbind(
    service: Any, ctx: HandlerContext, p: AgentsUnbindRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        removed = service.unbind(
            agent_id=p.agent_id,
            bind=p.bind,
            all=p.all,
            actor_agent_id=p.actor_agent_id,
        )
    except (KeyError, ValueError, PermissionError, RuntimeError) as exc:
        raise _invalid_management_error(exc)
    return AgentsUnbindResponse(removed=removed)


async def _handle_agents_start(
    service: Any, ctx: HandlerContext, p: AgentLifecycleRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        status = await asyncio.to_thread(
            service.start,
            p.agent_id,
            router_endpoint=p.router_endpoint or "",
            router_token=p.router_token or "",
        )
    except (KeyError, ValueError, PermissionError, RuntimeError) as exc:
        raise _invalid_management_error(exc)
    return AgentLifecycleResponse(status=_camelise(status))


async def _handle_agents_stop(
    service: Any, ctx: HandlerContext, p: AgentLifecycleRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        status = service.stop(p.agent_id)
    except (KeyError, ValueError, PermissionError, RuntimeError) as exc:
        raise _invalid_management_error(exc)
    return AgentLifecycleResponse(status=_camelise(status))


async def _handle_agents_restart(
    service: Any, ctx: HandlerContext, p: AgentLifecycleRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        status = await asyncio.to_thread(
            service.restart,
            p.agent_id,
            router_endpoint=p.router_endpoint or "",
            router_token=p.router_token or "",
        )
    except (KeyError, ValueError, PermissionError, RuntimeError) as exc:
        raise _invalid_management_error(exc)
    return AgentLifecycleResponse(status=_camelise(status))


async def _handle_agents_health(
    service: Any, ctx: HandlerContext, p: AgentHealthRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        health = service.health(p.agent_id)
    except (KeyError, ValueError, PermissionError, RuntimeError) as exc:
        raise _invalid_management_error(exc)
    return AgentHealthResponse(health=_camelise(health))


async def _handle_agents_grants(
    service: Any, ctx: HandlerContext, p: AgentsGrantsRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    return AgentsGrantsResponse(grants=_camelise(service.grants(agent_id=p.agent_id)))


async def _handle_agents_grant(
    service: Any, ctx: HandlerContext, p: AgentsGrantRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        grant = service.grant(
            p.agent_id,
            p.capability,
            scope=p.scope,
            resource=p.resource,
            workspace=p.workspace,
            expires_at=p.expires_at,
            actor_agent_id=p.actor_agent_id,
        )
    except (KeyError, ValueError, PermissionError, RuntimeError) as exc:
        raise _invalid_management_error(exc)
    return AgentsGrantResponse(grant=_camelise(grant))


async def _handle_agents_revoke_grant(
    service: Any, ctx: HandlerContext, p: AgentsRevokeGrantRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        grant = service.revoke_grant(p.grant_id, actor_agent_id=p.actor_agent_id)
    except (KeyError, ValueError, PermissionError, RuntimeError) as exc:
        raise _invalid_management_error(exc)
    return AgentsGrantResponse(grant=_camelise(grant))


async def _handle_agent_send(service: Any, ctx: HandlerContext, p: AgentSendRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        result = await service.send(
            agent_id=p.agent_id,
            message=p.message,
            session_id=p.session_id,
            deliver=p.deliver,
        )
    except RuntimeError as exc:
        message = str(exc)
        if "route unavailable" in message or "route stale" in message:
            return AgentSendResponse(
                delivered=False,
                error_code="route_unavailable",
                message=message,
            )
        raise _invalid_management_error(exc)
    return AgentSendResponse(delivered=True, result=_camelise(result))


# ---------------------------------------------------------------------------
# cron/* handler wrappers
# ---------------------------------------------------------------------------


async def _handle_cron_list(service: Any, ctx: HandlerContext, p: CronListRequest) -> BaseModel:
    del ctx
    tasks = await service.list_tasks(include_completed=p.include_completed)
    return CronListResponse(jobs=[_cron_task_to_wire(task) for task in tasks])


async def _handle_cron_create(service: Any, ctx: HandlerContext, p: CronCreateRequest) -> BaseModel:
    del ctx
    try:
        task = await service.create_task(
            schedule_expr=p.schedule,
            prompt=p.prompt,
            description=p.description or "",
            recurring=p.recurring,
        )
    except ValueError as exc:
        raise _invalid_management_error(exc)
    return CronCreateResponse(job=_cron_task_to_wire(task))


async def _handle_cron_delete(service: Any, ctx: HandlerContext, p: CronDeleteRequest) -> BaseModel:
    del ctx
    deleted = await service.delete_task(p.id)
    return CronDeleteResponse(id=p.id, deleted=deleted)


def _cron_task_to_wire(task: Any) -> dict[str, Any]:
    schedule = task.schedule
    return {
        "id": task.id,
        "ownerAgentId": task.owner_agent_id,
        "schedule": getattr(schedule, "expr", "") or getattr(schedule, "kind", ""),
        "scheduleKind": getattr(getattr(schedule, "kind", ""), "value", getattr(schedule, "kind", "")),
        "prompt": task.prompt,
        "description": task.description,
        "recurring": task.recurring,
        "durable": task.durable,
        "status": getattr(task.status, "value", task.status),
        "fireCount": task.fire_count,
        "createdAt": task.created_at,
        "lastFiredAt": task.last_fired_at,
        "nextFireAt": task.next_fire_at,
    }


# ---------------------------------------------------------------------------
# memory/* handler wrappers
# ---------------------------------------------------------------------------


async def _handle_memory_list(service: Any, ctx: HandlerContext, p: MemoryListRequest) -> BaseModel:
    del ctx
    return MemoryListResponse(memories=_camelise(service.list_records(category=p.category)))


async def _handle_memory_show(service: Any, ctx: HandlerContext, p: MemoryShowRequest) -> BaseModel:
    del ctx
    try:
        memory = service.read_record(p.name)
    except KeyError as exc:
        raise _invalid_management_error(exc)
    return MemoryShowResponse(memory=_camelise(memory))


async def _handle_memory_delete(service: Any, ctx: HandlerContext, p: MemoryDeleteRequest) -> BaseModel:
    del ctx
    try:
        result = service.delete_record(p.name, confirm=p.confirm)
    except (KeyError, PermissionError) as exc:
        raise _invalid_management_error(exc)
    return MemoryDeleteResponse.model_validate(_camelise(result))


# ---------------------------------------------------------------------------
# gateways/* handler wrappers
# ---------------------------------------------------------------------------


async def _handle_gateways_list(
    service: Any, ctx: HandlerContext, p: GatewaysListRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    return GatewaysListResponse(gateways=_camelise(service.list()))


async def _handle_gateways_create(
    service: Any, ctx: HandlerContext, p: GatewayCreateRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        gateway = service.create(
            gateway_id=p.gateway_id,
            gateway_type=p.gateway_type,
            config=p.config,
            enabled=p.enabled,
            actor=p.actor_agent_id,
        )
    except (KeyError, ValueError, PermissionError, RuntimeError) as exc:
        raise _invalid_management_error(exc)
    return GatewayRecordResponse(gateway=_camelise(gateway))


async def _handle_gateways_status(
    service: Any, ctx: HandlerContext, p: GatewaysStatusRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        status = service.status(p.gateway_id)
    except KeyError as exc:
        raise _invalid_management_error(exc)
    return GatewaysStatusResponse(status=_camelise(status))


async def _handle_gateways_enable(
    service: Any, ctx: HandlerContext, p: GatewayIdRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        revision = service.enable(p.gateway_id, actor=p.actor_agent_id)
    except KeyError as exc:
        raise _invalid_management_error(exc)
    return GatewayRevisionResponse(gateway_id=p.gateway_id, revision=revision)


async def _handle_gateways_delete(
    service: Any, ctx: HandlerContext, p: GatewayDeleteRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        result = service.delete(p.gateway_id, confirm=p.confirm, actor=p.actor_agent_id)
    except (KeyError, ValueError, PermissionError) as exc:
        raise _invalid_management_error(exc)
    return GatewayDeleteResponse.model_validate(result)


async def _handle_gateways_disable(
    service: Any, ctx: HandlerContext, p: GatewayIdRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        revision = service.disable(p.gateway_id, actor=p.actor_agent_id)
    except KeyError as exc:
        raise _invalid_management_error(exc)
    return GatewayRevisionResponse(gateway_id=p.gateway_id, revision=revision)


async def _handle_gateways_reload(
    service: Any, ctx: HandlerContext, p: GatewayReloadRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        result = service.reload(p.gateway_id, fail=p.fail)
    except KeyError as exc:
        raise _invalid_management_error(exc)
    return GatewayReloadResponse(
        gateway_id=result.gateway_id,
        status=result.status,
        error=result.error,
    )


async def _handle_gateways_bindings(
    service: Any, ctx: HandlerContext, p: GatewayBindingsRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    return GatewayBindingsResponse(
        bindings=_camelise(service.bindings(gateway_id=p.gateway_id, agent_id=p.agent_id))
    )


async def _handle_gateways_bind(
    service: Any, ctx: HandlerContext, p: GatewayBindRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        binding = service.bind(
            gateway_id=p.gateway_id,
            channel_key=p.channel_key,
            agent_id=p.agent_id,
            session_id=p.session_id,
            actor=p.actor_agent_id,
        )
    except (KeyError, ValueError, PermissionError) as exc:
        raise _invalid_management_error(exc)
    return GatewayBindResponse(binding=_camelise(binding))


async def _handle_gateways_unbind(
    service: Any, ctx: HandlerContext, p: GatewayUnbindRequest
) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        service.unbind(p.binding_id, actor=p.actor_agent_id)
    except KeyError as exc:
        raise _invalid_management_error(exc)
    return GatewayUnbindResponse(unbound=True)


# ---------------------------------------------------------------------------
# mcp/* handler wrappers
# ---------------------------------------------------------------------------


async def _handle_mcp_list(service: Any, ctx: HandlerContext, p: MCPListRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    return MCPListResponse.model_validate(service.list())


async def _handle_mcp_read(service: Any, ctx: HandlerContext, p: MCPReadRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        return MCPReadResponse.model_validate(service.read(p.name))
    except KeyError as exc:
        raise _invalid_management_error(exc)


async def _handle_mcp_create(service: Any, ctx: HandlerContext, p: MCPWriteRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        return MCPWriteResponse.model_validate(
            service.create(
                p.name,
                p.config,
                expected_revision=p.expected_revision,
                actor=p.actor_agent_id,
            )
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        raise _invalid_management_error(exc)


async def _handle_mcp_update(service: Any, ctx: HandlerContext, p: MCPWriteRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        return MCPWriteResponse.model_validate(
            service.update(
                p.name,
                p.config,
                expected_revision=p.expected_revision,
                actor=p.actor_agent_id,
            )
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        raise _invalid_management_error(exc)


async def _handle_mcp_delete(service: Any, ctx: HandlerContext, p: MCPDeleteRequest) -> BaseModel:
    del ctx
    _require_primary(p.actor_agent_id)
    try:
        return MCPDeleteResponse.model_validate(
            service.delete(
                p.name,
                expected_revision=p.expected_revision,
                actor=p.actor_agent_id,
            )
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        raise _invalid_management_error(exc)


# ---------------------------------------------------------------------------
# commands/* handler wrappers
# ---------------------------------------------------------------------------


async def _handle_commands_list(cm: Any, ctx: HandlerContext, p: ListCommandsRequest) -> BaseModel:
    del ctx, p
    commands = [CommandEntry.model_validate(item) for item in cm.list_command_dicts()]
    return ListCommandsResponse(commands=commands)


# ---------------------------------------------------------------------------
# skills/* handler wrappers
# ---------------------------------------------------------------------------


async def _handle_skills_list(sm: Any, ctx: HandlerContext, p: SkillsListRequest) -> BaseModel:
    del ctx
    records = [SkillRecordEntry.model_validate(asdict(item)) for item in sm.list_skill_records()]
    commands = []
    if p.include_commands:
        commands = [
            SkillCommandEntry(
                name=record.name,
                command=record.command,
                aliases=list(record.aliases),
            )
            for record in records
            if record.command
        ]
    return SkillsListResponse(skills=records, commands=commands)


async def _handle_skills_inspect(
    sm: Any, ctx: HandlerContext, p: SkillsInspectRequest
) -> BaseModel:
    del ctx
    result = sm.inspect_skill(p.name)
    if result is None:
        raise InvalidParams(f"Unknown skill: {p.name}")
    return SkillsInspectResponse(skill=SkillInspectEntry.model_validate(asdict(result)))


async def _handle_skills_refresh(
    sm: Any, ctx: HandlerContext, p: SkillsRefreshRequest
) -> BaseModel:
    del ctx, p
    return SkillsRefreshResponse.model_validate(sm.refresh())


# ---------------------------------------------------------------------------
# web_fetch/* handler wrappers
# ---------------------------------------------------------------------------


async def _handle_web_fetch_backend_options(
    tm: Any,
    ctx: HandlerContext,
    p: WebFetchBackendOptionsRequest,
) -> BaseModel:
    del ctx, p
    return WebFetchBackendOptionsResponse.model_validate(tm.web_fetch_backend_options())


async def _handle_web_fetch_set_backend(
    tm: Any,
    ctx: HandlerContext,
    p: SetWebFetchBackendRequest,
) -> BaseModel:
    del ctx
    try:
        result = await tm.set_web_fetch_backend(
            p.backend,
            run_setup=p.run_setup,
            api_key=p.api_key,
        )
    except ValueError as exc:
        raise InvalidParams(str(exc))
    return SetWebFetchBackendResponse.model_validate(result)


async def _handle_web_fetch_get_config(
    tm: Any,
    ctx: HandlerContext,
    p: WebFetchConfigRequest,
) -> BaseModel:
    del ctx, p
    return WebFetchConfigResponse.model_validate(tm.web_fetch_config())


async def _handle_web_fetch_set_config(
    tm: Any,
    ctx: HandlerContext,
    p: SetWebFetchConfigRequest,
) -> BaseModel:
    del ctx
    try:
        result = await tm.set_web_fetch_config_value(p.path, p.value)
    except ValueError as exc:
        raise InvalidParams(str(exc))
    return SetWebFetchConfigResponse.model_validate(result)


async def _handle_web_bridge_status(
    tm: Any,
    ctx: HandlerContext,
    p: WebBridgeStatusRequest,
) -> BaseModel:
    del ctx
    return WebBridgeStatusResponse.model_validate(
        tm.web_bridge_status(include_pairing_token=p.include_pairing_token)
    )


async def _handle_web_bridge_pair_start(
    tm: Any,
    ctx: HandlerContext,
    p: WebBridgePairStartRequest,
) -> BaseModel:
    del ctx, p
    return WebBridgeStatusResponse.model_validate(tm.web_bridge_pair_start())


async def _handle_web_bridge_pair_reset(
    tm: Any,
    ctx: HandlerContext,
    p: WebBridgePairResetRequest,
) -> BaseModel:
    del ctx, p
    return WebBridgeStatusResponse.model_validate(await tm.web_bridge_pair_reset())


# ---------------------------------------------------------------------------
# Dispatch tables
# ---------------------------------------------------------------------------

REQUEST_DISPATCH: dict[str, RequestSpec] = {
    MustangMethod.COMMANDS_LIST: RequestSpec(
        handler=_handle_commands_list,
        params_type=ListCommandsRequest,
        result_type=ListCommandsResponse,
        target="commands",
    ),
    MustangMethod.SKILLS_LIST: RequestSpec(
        handler=_handle_skills_list,
        params_type=SkillsListRequest,
        result_type=SkillsListResponse,
        target="skills",
    ),
    MustangMethod.SKILLS_INSPECT: RequestSpec(
        handler=_handle_skills_inspect,
        params_type=SkillsInspectRequest,
        result_type=SkillsInspectResponse,
        target="skills",
    ),
    MustangMethod.SKILLS_REFRESH: RequestSpec(
        handler=_handle_skills_refresh,
        params_type=SkillsRefreshRequest,
        result_type=SkillsRefreshResponse,
        target="skills",
    ),
    MustangMethod.AGENTS_LIST: RequestSpec(
        handler=_handle_agents_list,
        params_type=AgentsListRequest,
        result_type=AgentsListResponse,
        target="agents",
    ),
    MustangMethod.AGENTS_ADD: RequestSpec(
        handler=_handle_agents_add,
        params_type=AgentsAddRequest,
        result_type=AgentRecordResponse,
        target="agents",
    ),
    MustangMethod.AGENTS_DELETE: RequestSpec(
        handler=_handle_agents_delete,
        params_type=AgentsDeleteRequest,
        result_type=AgentsDeleteResponse,
        target="agents",
    ),
    MustangMethod.AGENTS_SET_IDENTITY: RequestSpec(
        handler=_handle_agents_set_identity,
        params_type=AgentsSetIdentityRequest,
        result_type=AgentRecordResponse,
        target="agents",
    ),
    MustangMethod.AGENTS_BINDINGS: RequestSpec(
        handler=_handle_agents_bindings,
        params_type=AgentsBindingsRequest,
        result_type=AgentsBindingsResponse,
        target="agents",
    ),
    MustangMethod.AGENTS_BIND: RequestSpec(
        handler=_handle_agents_bind,
        params_type=AgentsBindRequest,
        result_type=AgentsBindResponse,
        target="agents",
    ),
    MustangMethod.AGENTS_UNBIND: RequestSpec(
        handler=_handle_agents_unbind,
        params_type=AgentsUnbindRequest,
        result_type=AgentsUnbindResponse,
        target="agents",
    ),
    MustangMethod.AGENTS_START: RequestSpec(
        handler=_handle_agents_start,
        params_type=AgentLifecycleRequest,
        result_type=AgentLifecycleResponse,
        target="agents",
    ),
    MustangMethod.AGENTS_STOP: RequestSpec(
        handler=_handle_agents_stop,
        params_type=AgentLifecycleRequest,
        result_type=AgentLifecycleResponse,
        target="agents",
    ),
    MustangMethod.AGENTS_RESTART: RequestSpec(
        handler=_handle_agents_restart,
        params_type=AgentLifecycleRequest,
        result_type=AgentLifecycleResponse,
        target="agents",
    ),
    MustangMethod.AGENTS_HEALTH: RequestSpec(
        handler=_handle_agents_health,
        params_type=AgentHealthRequest,
        result_type=AgentHealthResponse,
        target="agents",
    ),
    MustangMethod.AGENTS_GRANTS: RequestSpec(
        handler=_handle_agents_grants,
        params_type=AgentsGrantsRequest,
        result_type=AgentsGrantsResponse,
        target="agents",
    ),
    MustangMethod.AGENTS_GRANT: RequestSpec(
        handler=_handle_agents_grant,
        params_type=AgentsGrantRequest,
        result_type=AgentsGrantResponse,
        target="agents",
    ),
    MustangMethod.AGENTS_REVOKE_GRANT: RequestSpec(
        handler=_handle_agents_revoke_grant,
        params_type=AgentsRevokeGrantRequest,
        result_type=AgentsGrantResponse,
        target="agents",
    ),
    MustangMethod.AGENT_SEND: RequestSpec(
        handler=_handle_agent_send,
        params_type=AgentSendRequest,
        result_type=AgentSendResponse,
        target="agents",
    ),
    MustangMethod.CRON_LIST: RequestSpec(
        handler=_handle_cron_list,
        params_type=CronListRequest,
        result_type=CronListResponse,
        target="schedule",
    ),
    MustangMethod.CRON_CREATE: RequestSpec(
        handler=_handle_cron_create,
        params_type=CronCreateRequest,
        result_type=CronCreateResponse,
        target="schedule",
    ),
    MustangMethod.CRON_DELETE: RequestSpec(
        handler=_handle_cron_delete,
        params_type=CronDeleteRequest,
        result_type=CronDeleteResponse,
        target="schedule",
    ),
    MustangMethod.MEMORY_LIST: RequestSpec(
        handler=_handle_memory_list,
        params_type=MemoryListRequest,
        result_type=MemoryListResponse,
        target="memory",
    ),
    MustangMethod.MEMORY_SHOW: RequestSpec(
        handler=_handle_memory_show,
        params_type=MemoryShowRequest,
        result_type=MemoryShowResponse,
        target="memory",
    ),
    MustangMethod.MEMORY_DELETE: RequestSpec(
        handler=_handle_memory_delete,
        params_type=MemoryDeleteRequest,
        result_type=MemoryDeleteResponse,
        target="memory",
    ),
    MustangMethod.GATEWAYS_LIST: RequestSpec(
        handler=_handle_gateways_list,
        params_type=GatewaysListRequest,
        result_type=GatewaysListResponse,
        target="gateways",
    ),
    MustangMethod.GATEWAYS_CREATE: RequestSpec(
        handler=_handle_gateways_create,
        params_type=GatewayCreateRequest,
        result_type=GatewayRecordResponse,
        target="gateways",
    ),
    MustangMethod.GATEWAYS_STATUS: RequestSpec(
        handler=_handle_gateways_status,
        params_type=GatewaysStatusRequest,
        result_type=GatewaysStatusResponse,
        target="gateways",
    ),
    MustangMethod.GATEWAYS_ENABLE: RequestSpec(
        handler=_handle_gateways_enable,
        params_type=GatewayIdRequest,
        result_type=GatewayRevisionResponse,
        target="gateways",
    ),
    MustangMethod.GATEWAYS_DELETE: RequestSpec(
        handler=_handle_gateways_delete,
        params_type=GatewayDeleteRequest,
        result_type=GatewayDeleteResponse,
        target="gateways",
    ),
    MustangMethod.GATEWAYS_DISABLE: RequestSpec(
        handler=_handle_gateways_disable,
        params_type=GatewayIdRequest,
        result_type=GatewayRevisionResponse,
        target="gateways",
    ),
    MustangMethod.GATEWAYS_RELOAD: RequestSpec(
        handler=_handle_gateways_reload,
        params_type=GatewayReloadRequest,
        result_type=GatewayReloadResponse,
        target="gateways",
    ),
    MustangMethod.GATEWAYS_BINDINGS: RequestSpec(
        handler=_handle_gateways_bindings,
        params_type=GatewayBindingsRequest,
        result_type=GatewayBindingsResponse,
        target="gateways",
    ),
    MustangMethod.GATEWAYS_BIND: RequestSpec(
        handler=_handle_gateways_bind,
        params_type=GatewayBindRequest,
        result_type=GatewayBindResponse,
        target="gateways",
    ),
    MustangMethod.GATEWAYS_UNBIND: RequestSpec(
        handler=_handle_gateways_unbind,
        params_type=GatewayUnbindRequest,
        result_type=GatewayUnbindResponse,
        target="gateways",
    ),
    MustangMethod.MCP_LIST: RequestSpec(
        handler=_handle_mcp_list,
        params_type=MCPListRequest,
        result_type=MCPListResponse,
        target="mcp",
    ),
    MustangMethod.MCP_READ: RequestSpec(
        handler=_handle_mcp_read,
        params_type=MCPReadRequest,
        result_type=MCPReadResponse,
        target="mcp",
    ),
    MustangMethod.MCP_CREATE: RequestSpec(
        handler=_handle_mcp_create,
        params_type=MCPWriteRequest,
        result_type=MCPWriteResponse,
        target="mcp",
    ),
    MustangMethod.MCP_UPDATE: RequestSpec(
        handler=_handle_mcp_update,
        params_type=MCPWriteRequest,
        result_type=MCPWriteResponse,
        target="mcp",
    ),
    MustangMethod.MCP_DELETE: RequestSpec(
        handler=_handle_mcp_delete,
        params_type=MCPDeleteRequest,
        result_type=MCPDeleteResponse,
        target="mcp",
    ),
    MustangMethod.GLOBAL_BACKUP: RequestSpec(
        handler=_handle_global_backup,
        params_type=GlobalBackupRequest,
        result_type=GlobalBackupResponse,
        target="global",
    ),
    MustangMethod.GLOBAL_BACKUPS: RequestSpec(
        handler=_handle_global_backups,
        params_type=GlobalBackupsRequest,
        result_type=GlobalBackupsResponse,
        target="global",
    ),
    MustangMethod.GLOBAL_EXPORT: RequestSpec(
        handler=_handle_global_export,
        params_type=GlobalExportRequest,
        result_type=GlobalExportResponse,
        target="global",
    ),
    MustangMethod.GLOBAL_IMPORT: RequestSpec(
        handler=_handle_global_import,
        params_type=GlobalImportRequest,
        result_type=GlobalImportResponse,
        target="global",
    ),
    MustangMethod.GLOBAL_RESTORE: RequestSpec(
        handler=_handle_global_restore,
        params_type=GlobalRestoreRequest,
        result_type=GlobalRestoreResponse,
        target="global",
    ),
    MustangMethod.FLAGS_LIST: RequestSpec(
        handler=_handle_flags_list,
        params_type=FlagsListRequest,
        result_type=FlagsListResponse,
        target="flags",
    ),
    MustangMethod.FLAGS_READ: RequestSpec(
        handler=_handle_flags_read,
        params_type=FlagsReadRequest,
        result_type=FlagsReadResponse,
        target="flags",
    ),
    MustangMethod.FLAGS_SET: RequestSpec(
        handler=_handle_flags_set,
        params_type=FlagsSetRequest,
        result_type=FlagsWriteResponse,
        target="flags",
    ),
    MustangMethod.FLAGS_RESET: RequestSpec(
        handler=_handle_flags_reset,
        params_type=FlagsResetRequest,
        result_type=FlagsWriteResponse,
        target="flags",
    ),
    MustangMethod.SECRETS_LIST: RequestSpec(
        handler=_handle_secrets_list,
        params_type=SecretsListRequest,
        result_type=SecretsListResponse,
        target="secrets",
    ),
    MustangMethod.SECRETS_AUDIT: RequestSpec(
        handler=_handle_secrets_audit,
        params_type=SecretsAuditRequest,
        result_type=SecretsAuditResponse,
        target="secrets",
    ),
    MustangMethod.SECRETS_RENAME: RequestSpec(
        handler=_handle_secrets_rename,
        params_type=SecretsRenameRequest,
        result_type=SecretsRenameResponse,
        target="secrets",
    ),
    MustangMethod.SECRETS_DELETE: RequestSpec(
        handler=_handle_secrets_delete,
        params_type=SecretsDeleteRequest,
        result_type=SecretsDeleteResponse,
        target="secrets",
    ),
    MustangMethod.WEB_FETCH_BACKEND_OPTIONS: RequestSpec(
        handler=_handle_web_fetch_backend_options,
        params_type=WebFetchBackendOptionsRequest,
        result_type=WebFetchBackendOptionsResponse,
        target="tools",
    ),
    MustangMethod.WEB_FETCH_SET_BACKEND: RequestSpec(
        handler=_handle_web_fetch_set_backend,
        params_type=SetWebFetchBackendRequest,
        result_type=SetWebFetchBackendResponse,
        target="tools",
    ),
    MustangMethod.WEB_FETCH_GET_CONFIG: RequestSpec(
        handler=_handle_web_fetch_get_config,
        params_type=WebFetchConfigRequest,
        result_type=WebFetchConfigResponse,
        target="tools",
    ),
    MustangMethod.WEB_FETCH_SET_CONFIG: RequestSpec(
        handler=_handle_web_fetch_set_config,
        params_type=SetWebFetchConfigRequest,
        result_type=SetWebFetchConfigResponse,
        target="tools",
    ),
    MustangMethod.WEB_BRIDGE_STATUS: RequestSpec(
        handler=_handle_web_bridge_status,
        params_type=WebBridgeStatusRequest,
        result_type=WebBridgeStatusResponse,
        target="tools",
    ),
    MustangMethod.WEB_BRIDGE_PAIR_START: RequestSpec(
        handler=_handle_web_bridge_pair_start,
        params_type=WebBridgePairStartRequest,
        result_type=WebBridgeStatusResponse,
        target="tools",
    ),
    MustangMethod.WEB_BRIDGE_PAIR_RESET: RequestSpec(
        handler=_handle_web_bridge_pair_reset,
        params_type=WebBridgePairResetRequest,
        result_type=WebBridgeStatusResponse,
        target="tools",
    ),
    # session/* -- routed to SessionHandler (SessionManager)
    AcpMethod.SESSION_NEW: RequestSpec(
        handler=_handle_new,
        params_type=NewSessionRequest,
        result_type=NewSessionResult,
        target="session",
    ),
    AcpMethod.SESSION_LOAD: RequestSpec(
        handler=_handle_load,
        params_type=LoadSessionRequest,
        result_type=LoadSessionResult,
        target="session",
    ),
    AcpMethod.SESSION_RESUME: RequestSpec(
        handler=_handle_resume,
        params_type=ResumeSessionRequest,
        result_type=ResumeSessionResult,
        target="session",
    ),
    AcpMethod.SESSION_CLOSE: RequestSpec(
        handler=_handle_close,
        params_type=CloseSessionRequest,
        result_type=CloseSessionResult,
        target="session",
    ),
    AcpMethod.SESSION_LIST: RequestSpec(
        handler=_handle_list,
        params_type=ListSessionsRequest,
        result_type=ListSessionsResult,
        target="session",
    ),
    AcpMethod.SESSION_PROMPT: RequestSpec(
        handler=_handle_prompt,
        params_type=PromptRequest,
        result_type=PromptResult,
        target="session",
    ),
    MustangMethod.SESSION_ACTIVATE_SKILL: RequestSpec(
        handler=_handle_activate_skill,
        params_type=ActivateSkillRequest,
        result_type=ActivateSkillResponse,
        target="session",
    ),
    MustangMethod.SESSION_EXECUTE_SHELL: RequestSpec(
        handler=_handle_execute_shell,
        params_type=ExecuteShellRequest,
        result_type=ExecuteShellResponse,
        target="session",
    ),
    MustangMethod.SESSION_EXECUTE_PYTHON: RequestSpec(
        handler=_handle_execute_python,
        params_type=ExecutePythonRequest,
        result_type=ExecutePythonResponse,
        target="session",
    ),
    MustangMethod.SESSION_CANCEL_EXECUTION: RequestSpec(
        handler=_handle_cancel_execution,
        params_type=CancelExecutionRequest,
        result_type=CancelExecutionResponse,
        target="session",
    ),
    AcpMethod.SESSION_SET_MODE: RequestSpec(
        handler=_handle_set_mode,
        params_type=SetSessionModeRequest,
        result_type=SetModeResult,
        target="session",
    ),
    AcpMethod.SESSION_SET_CONFIG_OPTION: RequestSpec(
        handler=_handle_set_config_option,
        params_type=SetSessionConfigOptionRequest,
        result_type=SetConfigOptionResult,
        target="session",
    ),
    MustangMethod.SESSION_RENAME: RequestSpec(
        handler=_handle_rename_session,
        params_type=RenameSessionRequest,
        result_type=RenameSessionResult,
        target="session",
    ),
    MustangMethod.SESSION_ARCHIVE: RequestSpec(
        handler=_handle_archive_session,
        params_type=ArchiveSessionRequest,
        result_type=ArchiveSessionResult,
        target="session",
    ),
    MustangMethod.SESSION_DELETE: RequestSpec(
        handler=_handle_delete_session,
        params_type=DeleteSessionRequest,
        result_type=DeleteSessionResult,
        target="session",
    ),
    MustangMethod.SESSION_GET_USAGE: RequestSpec(
        handler=_handle_get_usage,
        params_type=GetUsageRequest,
        result_type=GetUsageResponse,
        target="session",
    ),
    MustangMethod.SESSION_TOOL_SNAPSHOT: RequestSpec(
        handler=_handle_tool_snapshot,
        params_type=ToolSnapshotRequest,
        result_type=ToolSnapshotResponse,
        target="session",
    ),
    # model/* -- routed to ModelHandler (LLMManager)
    MustangMethod.MODEL_PROFILE_LIST: RequestSpec(
        handler=_handle_profile_list,
        params_type=ListProfilesRequest,
        result_type=ListProfilesResponse,
        target="model",
    ),
    MustangMethod.MODEL_PROVIDER_LIST: RequestSpec(
        handler=_handle_provider_list,
        params_type=ListProvidersRequest,
        result_type=ListProvidersResponse,
        target="model",
    ),
    MustangMethod.MODEL_PROVIDER_ADD: RequestSpec(
        handler=_handle_provider_add,
        params_type=AddProviderRequest,
        result_type=AddProviderResponse,
        target="model",
    ),
    MustangMethod.MODEL_PROVIDER_REMOVE: RequestSpec(
        handler=_handle_provider_remove,
        params_type=RemoveProviderRequest,
        result_type=RemoveProviderResponse,
        target="model",
    ),
    MustangMethod.MODEL_PROVIDER_REFRESH: RequestSpec(
        handler=_handle_provider_refresh,
        params_type=RefreshModelsRequest,
        result_type=RefreshModelsResponse,
        target="model",
    ),
    MustangMethod.MODEL_SET_CURRENT: RequestSpec(
        handler=_handle_set_current,
        params_type=SetCurrentModelRequest,
        result_type=SetCurrentModelResponse,
        target="model",
    ),
    MustangMethod.MODEL_ADD: RequestSpec(
        handler=_handle_model_add,
        params_type=AddModelRequest,
        result_type=UpdateModelResponse,
        target="model",
    ),
    MustangMethod.MODEL_UPDATE: RequestSpec(
        handler=_handle_model_update,
        params_type=UpdateModelRequest,
        result_type=UpdateModelResponse,
        target="model",
    ),
    # secrets/* -- routed to SecretManager (bootstrap service)
    MustangMethod.SECRETS_AUTH: RequestSpec(
        handler=_handle_auth,
        params_type=AuthRequest,
        result_type=AuthResult,
        target="secrets",
    ),
}

NOTIFICATION_DISPATCH: dict[str, NotificationSpec] = {
    AcpMethod.SESSION_CANCEL: NotificationSpec(
        handler=_handle_cancel,
        params_type=CancelNotification,
    ),
    MustangMethod.SESSION_CANCEL_EXECUTION: NotificationSpec(
        handler=_notify_cancel_execution,
        params_type=CancelExecutionRequest,
    ),
}

OUTGOING_NOTIFICATIONS = {AcpMethod.SESSION_UPDATE, MustangMethod.SESSION_EXECUTION_UPDATE}
OUTGOING_REQUESTS = {AcpMethod.SESSION_REQUEST_PERMISSION}
