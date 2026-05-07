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

``AcpSessionHandler._route_request`` reads ``target`` to select the
right handler object from ``KernelModuleTable``.  Adding a new target
is a two-step change: add the ``Literal`` value here and add the
matching ``_get_<target>_handler()`` branch in ``session_handler.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel
from pydantic.alias_generators import to_camel

from kernel.llm.config import ModelRef
from kernel.protocol.acp.namespaces import AcpMethod, MustangMethod
from kernel.protocol.acp.schemas.commands import (
    CommandEntry,
    ListCommandsRequest,
    ListCommandsResponse,
)
from kernel.protocol.acp.schemas.model import (
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
from kernel.protocol.acp.schemas.session import (
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
from kernel.protocol.interfaces.contracts.archive_session_params import ArchiveSessionParams
from kernel.protocol.interfaces.contracts.activate_skill_params import ActivateSkillParams
from kernel.protocol.interfaces.contracts.archive_session_result import ArchiveSessionResult
from kernel.protocol.interfaces.contracts.close_session_params import CloseSessionParams
from kernel.protocol.interfaces.contracts.close_session_result import CloseSessionResult
from kernel.protocol.interfaces.contracts.delete_session_params import DeleteSessionParams
from kernel.protocol.interfaces.contracts.delete_session_result import DeleteSessionResult
from kernel.protocol.interfaces.contracts.cancel_execution_params import (
    CancelExecutionParams,
)
from kernel.protocol.interfaces.contracts.add_provider_params import (
    AddProviderParams,
)
from kernel.protocol.interfaces.contracts.add_model_params import AddModelParams
from kernel.protocol.interfaces.contracts.cancel_params import CancelParams
from kernel.protocol.interfaces.contracts.execute_python_params import (
    ExecutePythonParams,
)
from kernel.protocol.interfaces.contracts.execute_shell_params import ExecuteShellParams
from kernel.protocol.interfaces.contracts.get_usage_params import GetUsageParams
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.protocol.interfaces.contracts.list_profiles_params import (
    ListProfilesParams,
)
from kernel.protocol.interfaces.contracts.list_providers_params import (
    ListProvidersParams,
)
from kernel.protocol.interfaces.contracts.list_sessions_params import (
    ListSessionsParams,
)
from kernel.protocol.interfaces.contracts.list_sessions_result import (
    ListSessionsResult,
)
from kernel.protocol.interfaces.contracts.load_session_params import (
    LoadSessionParams,
)
from kernel.protocol.interfaces.contracts.load_session_result import (
    LoadSessionResult,
)
from kernel.protocol.interfaces.contracts.new_session_params import (
    NewSessionParams,
)
from kernel.protocol.interfaces.contracts.new_session_result import (
    NewSessionResult,
)
from kernel.protocol.interfaces.contracts.prompt_params import PromptParams
from kernel.protocol.interfaces.contracts.prompt_result import PromptResult
from kernel.protocol.interfaces.contracts.rename_session_params import RenameSessionParams
from kernel.protocol.interfaces.contracts.rename_session_result import RenameSessionResult
from kernel.protocol.interfaces.contracts.resume_session_params import ResumeSessionParams
from kernel.protocol.interfaces.contracts.resume_session_result import ResumeSessionResult
from kernel.protocol.interfaces.contracts.refresh_models_params import (
    RefreshModelsParams,
)
from kernel.protocol.interfaces.contracts.remove_provider_params import (
    RemoveProviderParams,
)
from kernel.protocol.interfaces.contracts.set_config_option_params import (
    SetConfigOptionParams,
)
from kernel.protocol.interfaces.contracts.set_config_option_result import (
    SetConfigOptionResult,
)
from kernel.protocol.interfaces.contracts.set_current_model_params import (
    SetCurrentModelParams,
)
from kernel.protocol.interfaces.contracts.update_model_params import UpdateModelParams
from kernel.protocol.interfaces.contracts.update_model_result import UpdateModelResult
from kernel.protocol.interfaces.contracts.set_mode_params import SetModeParams
from kernel.protocol.interfaces.contracts.set_mode_result import SetModeResult
from kernel.protocol.acp.schemas.auth import AuthRequest, AuthResult
from kernel.protocol.interfaces.model_handler import ModelHandler
from kernel.protocol.interfaces.session_handler import SessionHandler

# Discriminator for which kernel subsystem handles a request.
HandlerTarget = Literal["session", "model", "secrets", "commands"]


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
    from kernel.protocol.interfaces.contracts.text_block import TextBlock
    from kernel.protocol.interfaces.contracts.image_block import ImageBlock
    from kernel.protocol.interfaces.contracts.resource_block import ResourceBlock
    from kernel.protocol.interfaces.contracts.resource_link_block import ResourceLinkBlock

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
    from kernel.protocol.interfaces.errors import InvalidParams
    from kernel.secrets.types import SecretNotFoundError
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
# commands/* handler wrappers
# ---------------------------------------------------------------------------


async def _handle_commands_list(cm: Any, ctx: HandlerContext, p: ListCommandsRequest) -> BaseModel:
    del ctx, p
    commands = [CommandEntry.model_validate(item) for item in cm.list_command_dicts()]
    return ListCommandsResponse(commands=commands)


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
