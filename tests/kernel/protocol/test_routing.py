"""Tests for ACP routing handler wrappers.

Each _handle_* function converts ACP wire-format params to internal
contract types and forwards to the appropriate handler. Tests verify
the conversion and delegation logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


from kernel.core.protocol.acp.namespaces import (
    MUSTANG_EXTENSION_PREFIX,
    AcpMethod,
    MethodKind,
    MustangMethod,
    classify_method,
)
from kernel.core.protocol.acp.routing import (
    REQUEST_DISPATCH,
    NOTIFICATION_DISPATCH,
    _handle_archive_session,
    _handle_close,
    _handle_cancel_execution,
    _handle_delete_session,
    _handle_execute_python,
    _handle_execute_shell,
    _handle_get_usage,
    _handle_new,
    _handle_load,
    _handle_list,
    _handle_prompt,
    _handle_set_mode,
    _handle_set_config_option,
    _handle_rename_session,
    _handle_resume,
    _handle_cancel,
    _handle_profile_list,
    _handle_provider_list,
    _handle_provider_add,
    _handle_provider_remove,
    _handle_provider_refresh,
    _handle_set_current,
    _handle_thinking_get,
    _handle_thinking_set,
    _handle_model_add,
    _handle_model_update,
)
from kernel.core.protocol.acp.schemas.model import (
    AddModelRequest,
    AddProviderRequest,
    GetThinkingRequest,
    ListProfilesRequest,
    ListProvidersRequest,
    RefreshModelsRequest,
    RemoveProviderRequest,
    SetCurrentModelRequest,
    SetThinkingRequest,
    UpdateModelRequest,
)
from kernel.core.protocol.acp.schemas.session import (
    ArchiveSessionRequest,
    CancelExecutionRequest,
    CancelNotification,
    CloseSessionRequest,
    DeleteSessionRequest,
    ExecutePythonRequest,
    ExecuteShellRequest,
    GetUsageRequest,
    ListSessionsRequest,
    LoadSessionRequest,
    NewSessionRequest,
    PromptRequest,
    RenameSessionRequest,
    ResumeSessionRequest,
    SetSessionConfigOptionRequest,
    SetSessionModeRequest,
)
from kernel.core.protocol.interfaces.contracts.archive_session_result import ArchiveSessionResult
from kernel.core.protocol.interfaces.contracts.close_session_result import CloseSessionResult
from kernel.core.protocol.interfaces.contracts.delete_session_result import DeleteSessionResult
from kernel.core.protocol.interfaces.contracts.get_usage_result import (
    ContextUsageSummary,
    EnvironmentUsageSummary,
    GetUsageResult,
    HistoryUsageSummary,
    MemoryUsageSummary,
    TokenUsageSummary,
)
from kernel.core.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.core.protocol.interfaces.contracts.execution_result import ExecutionResult
from kernel.core.protocol.interfaces.contracts.list_providers_result import (
    ListProvidersResult,
    ProviderInfo,
    ProviderTypeInfo,
)
from kernel.core.protocol.interfaces.contracts.list_profiles_result import (
    ListProfilesResult,
    ProfileInfo,
)
from kernel.core.protocol.interfaces.contracts.new_session_result import NewSessionResult
from kernel.core.protocol.interfaces.contracts.load_session_result import LoadSessionResult
from kernel.core.protocol.interfaces.contracts.list_sessions_result import ListSessionsResult
from kernel.core.protocol.interfaces.contracts.rename_session_result import RenameSessionResult
from kernel.core.protocol.interfaces.contracts.resume_session_result import ResumeSessionResult
from kernel.core.protocol.interfaces.contracts.set_mode_result import SetModeResult
from kernel.core.protocol.interfaces.contracts.set_config_option_result import (
    SetConfigOptionResult,
)
from kernel.core.protocol.interfaces.contracts.add_provider_result import AddProviderResult
from kernel.core.protocol.interfaces.contracts.remove_provider_result import RemoveProviderResult
from kernel.core.protocol.interfaces.contracts.refresh_models_result import RefreshModelsResult
from kernel.core.protocol.interfaces.contracts.set_current_model_result import (
    SetCurrentModelResult,
)
from kernel.core.protocol.interfaces.contracts.get_thinking_result import GetThinkingResult
from kernel.core.protocol.interfaces.contracts.set_thinking_result import SetThinkingResult
from kernel.core.protocol.interfaces.contracts.update_model_result import UpdateModelResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx() -> HandlerContext:
    return HandlerContext(conn=MagicMock(), sender=MagicMock(), request_id=1)


# ---------------------------------------------------------------------------
# Dispatch table structure
# ---------------------------------------------------------------------------


class TestDispatchTables:
    def test_all_session_methods_present(self) -> None:
        for method in [
            AcpMethod.SESSION_NEW,
            AcpMethod.SESSION_LOAD,
            AcpMethod.SESSION_RESUME,
            AcpMethod.SESSION_CLOSE,
            AcpMethod.SESSION_LIST,
            AcpMethod.SESSION_PROMPT,
            AcpMethod.SESSION_SET_MODE,
            AcpMethod.SESSION_SET_CONFIG_OPTION,
            MustangMethod.SESSION_EXECUTE_SHELL,
            MustangMethod.SESSION_EXECUTE_PYTHON,
            MustangMethod.SESSION_CANCEL_EXECUTION,
            MustangMethod.SESSION_RENAME,
            MustangMethod.SESSION_ARCHIVE,
            MustangMethod.SESSION_DELETE,
            MustangMethod.SESSION_GET_USAGE,
        ]:
            assert method in REQUEST_DISPATCH

    def test_all_model_methods_present(self) -> None:
        for method in [
            MustangMethod.MODEL_PROFILE_LIST,
            MustangMethod.MODEL_PROVIDER_LIST,
            MustangMethod.MODEL_PROVIDER_ADD,
            MustangMethod.MODEL_PROVIDER_REMOVE,
            MustangMethod.MODEL_PROVIDER_REFRESH,
            MustangMethod.MODEL_SET_CURRENT,
            MustangMethod.MODEL_ADD,
            MustangMethod.MODEL_UPDATE,
            MustangMethod.LLM_THINKING_GET,
            MustangMethod.LLM_THINKING_SET,
        ]:
            assert method in REQUEST_DISPATCH

    def test_cancel_notification(self) -> None:
        assert AcpMethod.SESSION_CANCEL in NOTIFICATION_DISPATCH
        assert MustangMethod.SESSION_CANCEL_EXECUTION in NOTIFICATION_DISPATCH

    def test_session_targets(self) -> None:
        for method in [AcpMethod.SESSION_NEW, AcpMethod.SESSION_LOAD, AcpMethod.SESSION_LIST]:
            assert REQUEST_DISPATCH[method].target == "session"

    def test_model_targets(self) -> None:
        for method in [
            MustangMethod.MODEL_PROVIDER_LIST,
            MustangMethod.MODEL_PROVIDER_ADD,
            MustangMethod.LLM_THINKING_GET,
        ]:
            assert REQUEST_DISPATCH[method].target == "model"


def _schema_methods() -> set[str]:
    schema_path = Path(__file__).parents[3] / "docs/kernel/references/acp/schema.json"
    schema = json.loads(schema_path.read_text())
    methods: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            method = value.get("x-method")
            if isinstance(method, str):
                methods.add(method)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)
    return methods


class TestSchemaAudit:
    def test_routed_methods_are_standard_or_declared_legacy(self) -> None:
        official = _schema_methods()
        routed = set(REQUEST_DISPATCH) | set(NOTIFICATION_DISPATCH)

        unsupported = {
            method
            for method in routed
            if classify_method(method, official) == MethodKind.UNSUPPORTED_OFFICIAL
        }
        assert unsupported == set()

    def test_mustang_extension_methods_are_declared(self) -> None:
        official = _schema_methods()
        routed = set(REQUEST_DISPATCH) | set(NOTIFICATION_DISPATCH)

        extensions = {method for method in routed if method.startswith(MUSTANG_EXTENSION_PREFIX)}
        assert extensions
        for extension in extensions:
            assert extension.startswith(MUSTANG_EXTENSION_PREFIX)
            assert extension not in official
            assert extension in routed
            assert classify_method(extension, official) == MethodKind.MUSTANG_EXTENSION

    def test_expected_official_gaps_are_visible(self) -> None:
        official = _schema_methods()
        routed = set(REQUEST_DISPATCH) | set(NOTIFICATION_DISPATCH)

        unsupported_official = official - routed
        assert AcpMethod.SESSION_CLOSE not in unsupported_official
        assert AcpMethod.SESSION_RESUME not in unsupported_official
        assert AcpMethod.TERMINAL_CREATE in unsupported_official
        assert AcpMethod.FS_READ_TEXT_FILE in unsupported_official


# ---------------------------------------------------------------------------
# Session handler wrappers
# ---------------------------------------------------------------------------


class TestHandleNew:
    async def test_delegates_to_session_handler(self) -> None:
        sh = MagicMock()
        sh.new = AsyncMock(return_value=NewSessionResult(session_id="sess-123"))
        params = NewSessionRequest(cwd="/tmp/test", mcp_servers=[])
        result = await _handle_new(sh, _ctx(), params)
        sh.new.assert_awaited_once()
        assert result.session_id == "sess-123"


class TestHandleLoad:
    async def test_delegates(self) -> None:
        sh = MagicMock()
        sh.load_session = AsyncMock(return_value=LoadSessionResult())
        params = LoadSessionRequest(session_id="sess-1", cwd="/tmp", mcp_servers=[])
        await _handle_load(sh, _ctx(), params)
        sh.load_session.assert_awaited_once()


class TestHandleResumeAndClose:
    async def test_resume_delegates_without_replay(self) -> None:
        sh = MagicMock()
        sh.resume_session = AsyncMock(return_value=ResumeSessionResult(replayed=False))
        result = await _handle_resume(
            sh,
            _ctx(),
            ResumeSessionRequest(session_id="sess-1", cwd="/tmp"),
        )
        sh.resume_session.assert_awaited_once()
        assert result.meta is None

    async def test_close_delegates(self) -> None:
        sh = MagicMock()
        sh.close_session = AsyncMock(return_value=CloseSessionResult())
        result = await _handle_close(
            sh,
            _ctx(),
            CloseSessionRequest(session_id="sess-1"),
        )
        sh.close_session.assert_awaited_once()
        assert result.meta is None


class TestHandleList:
    async def test_converts_sessions(self) -> None:
        from kernel.core.protocol.interfaces.contracts.list_sessions_result import SessionSummary

        sh = MagicMock()
        sh.list = AsyncMock(
            return_value=ListSessionsResult(
                sessions=[
                    SessionSummary(
                        session_id="s1",
                        cwd="/tmp",
                        updated_at="2026-01-01T00:00:00Z",
                        title="Test",
                    ),
                ],
                next_cursor=None,
            )
        )
        params = ListSessionsRequest()
        result = await _handle_list(sh, _ctx(), params)
        assert len(result.sessions) == 1
        assert result.sessions[0].session_id == "s1"

    async def test_reads_filters_from_meta(self) -> None:
        sh = MagicMock()
        sh.list = AsyncMock(return_value=ListSessionsResult(sessions=[], next_cursor=None))
        params = ListSessionsRequest(
            meta={
                "mustang.agent/sessionFilters": {
                    "includeArchived": True,
                    "archivedOnly": True,
                }
            }
        )
        await _handle_list(sh, _ctx(), params)
        forwarded = sh.list.await_args.args[1]
        assert forwarded.include_archived is True
        assert forwarded.archived_only is True

    async def test_moves_session_custom_fields_to_meta(self) -> None:
        from kernel.core.protocol.interfaces.contracts.list_sessions_result import SessionSummary

        sh = MagicMock()
        sh.list = AsyncMock(
            return_value=ListSessionsResult(
                sessions=[
                    SessionSummary(
                        session_id="s1",
                        cwd="/tmp",
                        updated_at="2026-01-01T00:00:00Z",
                        title="Test",
                        archived_at="2026-01-02T00:00:00Z",
                        title_source="user",
                    ),
                ],
            )
        )
        result = await _handle_list(sh, _ctx(), ListSessionsRequest())
        session = result.sessions[0]
        assert session.archived_at is None
        assert session.title_source is None
        assert session.meta["mustang.agent/session"]["archivedAt"] == "2026-01-02T00:00:00Z"
        assert session.meta["mustang.agent/session"]["titleSource"] == "user"


class TestHandlePrompt:
    async def test_reads_max_turns_from_meta(self) -> None:
        sh = MagicMock()
        sh.prompt = AsyncMock(return_value=MagicMock(stop_reason="end_turn"))
        params = PromptRequest(
            session_id="s1",
            prompt=[],
            meta={"mustang.agent/maxTurns": 3},
        )
        await _handle_prompt(sh, _ctx(), params)
        forwarded = sh.prompt.await_args.args[1]
        assert forwarded.max_turns == 3

    async def test_forwards_client_turn_id_meta(self) -> None:
        from kernel.core.protocol.interfaces.contracts.prompt_result import PromptResult

        sh = MagicMock()
        sh.prompt = AsyncMock(
            return_value=PromptResult(
                stop_reason="end_turn",
                meta={"mustang.agent/clientTurnId": "11111111-1111-4111-8111-111111111111"},
            )
        )
        params = PromptRequest(
            session_id="s1",
            prompt=[],
            meta={"mustang.agent/clientTurnId": "11111111-1111-4111-8111-111111111111"},
        )

        result = await _handle_prompt(sh, _ctx(), params)

        forwarded = sh.prompt.await_args.args[1]
        assert forwarded.meta == params.meta
        assert result.meta == params.meta


class TestHandleSetMode:
    async def test_delegates(self) -> None:
        sh = MagicMock()
        sh.set_mode = AsyncMock(return_value=SetModeResult())
        params = SetSessionModeRequest(session_id="s1", mode_id="plan")
        await _handle_set_mode(sh, _ctx(), params)
        sh.set_mode.assert_awaited_once()


class TestHandleSetConfigOption:
    async def test_delegates(self) -> None:
        from kernel.core.protocol.interfaces.contracts.session_config import ConfigOptionDescriptor

        sh = MagicMock()
        sh.set_config_option = AsyncMock(
            return_value=SetConfigOptionResult(
                config_options=[
                    ConfigOptionDescriptor(
                        config_id="mode",
                        name="Mode",
                        current_value="plan",
                        options=[],
                    )
                ]
            )
        )
        params = SetSessionConfigOptionRequest(
            session_id="s1",
            config_id="mode",
            value="plan",
        )
        result = await _handle_set_config_option(sh, _ctx(), params)
        assert len(result.config_options) == 1
        assert result.config_options[0]["configId"] == "mode"


class TestHandleLifecycleActions:
    async def test_rename_delegates(self) -> None:
        sh = MagicMock()
        sh.rename_session = AsyncMock(
            return_value=RenameSessionResult(
                session_id="s1",
                cwd="/tmp",
                updated_at="2026-04-28T00:00:00+00:00",
                title="Renamed",
                title_source="user",
            )
        )
        result = await _handle_rename_session(
            sh,
            _ctx(),
            RenameSessionRequest(session_id="s1", title="Renamed"),
        )
        assert result.session.title == "Renamed"
        assert result.session.title_source is None
        assert result.session.meta["mustang.agent/session"]["titleSource"] == "user"

    async def test_archive_delegates(self) -> None:
        sh = MagicMock()
        sh.archive_session = AsyncMock(
            return_value=ArchiveSessionResult(
                session_id="s1",
                cwd="/tmp",
                updated_at="2026-04-28T00:00:00+00:00",
                archived_at="2026-04-28T00:00:00+00:00",
            )
        )
        result = await _handle_archive_session(
            sh,
            _ctx(),
            ArchiveSessionRequest(session_id="s1", archived=True),
        )
        assert result.session.archived_at is None
        assert result.session.meta["mustang.agent/session"]["archivedAt"] is not None

    async def test_delete_delegates(self) -> None:
        sh = MagicMock()
        sh.delete_session = AsyncMock(return_value=DeleteSessionResult(deleted=True))
        result = await _handle_delete_session(
            sh,
            _ctx(),
            DeleteSessionRequest(session_id="s1", force=True),
        )
        assert result.deleted is True

    async def test_get_usage_delegates(self) -> None:
        sh = MagicMock()
        sh.get_usage = AsyncMock(
            return_value=GetUsageResult(
                session_id="s1",
                cwd="/tmp",
                kernel_version="1.0.0",
                tokens=TokenUsageSummary(input=10, output=5, total=15),
                context=ContextUsageSummary(total_tokens=15, percent=1.5),
                history=HistoryUsageSummary(turns=1),
                memory=MemoryUsageSummary(),
                environment=EnvironmentUsageSummary(),
            )
        )
        result = await _handle_get_usage(sh, _ctx(), GetUsageRequest(session_id="s1"))
        assert result.session_id == "s1"
        assert result.tokens.total == 15


class TestHandleCancel:
    async def test_delegates(self) -> None:
        sh = MagicMock()
        sh.cancel = AsyncMock()
        params = CancelNotification(session_id="s1")
        await _handle_cancel(sh, _ctx(), params)
        sh.cancel.assert_awaited_once()


class TestHandleUserRepl:
    async def test_execute_shell_delegates(self) -> None:
        sh = MagicMock()
        sh.execute_shell = AsyncMock(return_value=ExecutionResult(exit_code=0))
        result = await _handle_execute_shell(
            sh,
            _ctx(),
            ExecuteShellRequest(session_id="s1", command="echo hi", excludeFromContext=True),
        )
        sh.execute_shell.assert_awaited_once()
        assert result.exit_code == 0

    async def test_execute_python_delegates(self) -> None:
        sh = MagicMock()
        sh.execute_python = AsyncMock(return_value=ExecutionResult(exit_code=0))
        result = await _handle_execute_python(
            sh,
            _ctx(),
            ExecutePythonRequest(session_id="s1", code="1 + 1"),
        )
        sh.execute_python.assert_awaited_once()
        assert result.exit_code == 0

    async def test_cancel_execution_delegates(self) -> None:
        sh = MagicMock()
        sh.cancel_execution = AsyncMock()
        result = await _handle_cancel_execution(
            sh,
            _ctx(),
            CancelExecutionRequest(session_id="s1", kind="python"),
        )
        sh.cancel_execution.assert_awaited_once()
        assert result is not None


# ---------------------------------------------------------------------------
# Model handler wrappers
# ---------------------------------------------------------------------------


class TestHandleProfileList:
    async def test_converts_context_window(self) -> None:
        mh = MagicMock()
        mh.list_profiles = AsyncMock(
            return_value=ListProfilesResult(
                profiles=[
                    ProfileInfo(
                        name="anthropic/claude-opus-4-6",
                        provider_type="anthropic",
                        model_id="claude-opus-4-6",
                        context_window=200_000,
                        is_default=True,
                    ),
                ],
                default_model="anthropic/claude-opus-4-6",
            )
        )
        result = await _handle_profile_list(mh, _ctx(), ListProfilesRequest())
        assert len(result.profiles) == 1
        assert result.profiles[0].context_window == 200_000


class TestHandleProviderList:
    async def test_converts_providers(self) -> None:
        mh = MagicMock()
        mh.list_providers = AsyncMock(
            return_value=ListProvidersResult(
                providers=[
                    ProviderInfo(
                        name="anthropic",
                        provider_type="anthropic",
                        setting_fields=["api_key", "base_url"],
                        models=["claude-opus-4-6"],
                        context_windows={"claude-opus-4-6": 200_000},
                        display_names={"claude-opus-4-6": "Opus"},
                        roles={"default": True},
                    ),
                ],
                provider_type_options=[
                    ProviderTypeInfo(
                        provider_type="anthropic",
                        setting_fields=["api_key", "base_url"],
                    )
                ],
                current_used={"default": ["anthropic", "claude-opus-4-6"]},
                default_context_window=128_000,
            )
        )
        result = await _handle_provider_list(mh, _ctx(), ListProvidersRequest())
        assert len(result.providers) == 1
        assert result.providers[0].name == "anthropic"
        assert result.providers[0].setting_fields == ["api_key", "base_url"]
        assert result.providers[0].context_windows == {"claude-opus-4-6": 200_000}
        assert result.providers[0].display_names == {"claude-opus-4-6": "Opus"}
        assert result.provider_type_options[0].provider_type == "anthropic"
        assert result.current_used == {"default": ["anthropic", "claude-opus-4-6"]}
        assert result.default_context_window == 128_000


class TestHandleProviderAdd:
    async def test_delegates(self) -> None:
        mh = MagicMock()
        mh.add_provider = AsyncMock(
            return_value=AddProviderResult(name="bedrock", models=["model-a"]),
        )
        params = AddProviderRequest(
            name="bedrock",
            provider_type="bedrock",
            models=["model-a"],
        )
        result = await _handle_provider_add(mh, _ctx(), params)
        assert result.name == "bedrock"
        assert result.models == ["model-a"]


class TestHandleThinking:
    async def test_get_delegates(self) -> None:
        mh = MagicMock()
        mh.get_thinking = AsyncMock(return_value=GetThinkingResult(enabled=True))
        result = await _handle_thinking_get(mh, _ctx(), GetThinkingRequest())
        assert result.enabled is True
        mh.get_thinking.assert_awaited_once()

    async def test_set_delegates(self) -> None:
        mh = MagicMock()
        mh.set_thinking = AsyncMock(return_value=SetThinkingResult(enabled=False))
        result = await _handle_thinking_set(mh, _ctx(), SetThinkingRequest(enabled=False))
        assert result.enabled is False
        params = mh.set_thinking.await_args.args[1]
        assert params.enabled is False


class TestHandleProviderRemove:
    async def test_delegates(self) -> None:
        mh = MagicMock()
        mh.remove_provider = AsyncMock(return_value=RemoveProviderResult())
        params = RemoveProviderRequest(name="old")
        await _handle_provider_remove(mh, _ctx(), params)
        mh.remove_provider.assert_awaited_once()


class TestHandleProviderRefresh:
    async def test_delegates(self) -> None:
        mh = MagicMock()
        mh.refresh_models = AsyncMock(
            return_value=RefreshModelsResult(models=["m1", "m2"]),
        )
        params = RefreshModelsRequest(name="anthropic")
        result = await _handle_provider_refresh(mh, _ctx(), params)
        assert result.models == ["m1", "m2"]


class TestHandleSetCurrent:
    async def test_delegates(self) -> None:
        mh = MagicMock()
        mh.set_current_model = AsyncMock(
            return_value=SetCurrentModelResult(role="compact", model=["anthropic", "sonnet"]),
        )
        params = SetCurrentModelRequest(role="compact", provider="anthropic", model="sonnet")
        result = await _handle_set_current(mh, _ctx(), params)
        assert result.role == "compact"
        assert result.model == ["anthropic", "sonnet"]


class TestHandleModelAdd:
    async def test_delegates(self) -> None:
        mh = MagicMock()
        mh.add_model = AsyncMock(
            return_value=UpdateModelResult(
                model=["anthropic", "sonnet"],
                provider_type="anthropic",
                base_url=None,
                effective_base_url=None,
                aws_region=None,
                has_api_key=True,
                api_key_display="sk-anthropic-1234",
                has_aws_secret_key=False,
                aws_secret_key_display=None,
                setting_fields=["api_key", "base_url"],
                display_name="Sonnet",
                context_window=200_000,
                roles=["default"],
            ),
        )
        params = AddModelRequest(
            providerName="anthropic",
            providerType="anthropic",
            modelId="sonnet",
            displayName="Sonnet",
            contextWindow=200_000,
            roles=["default"],
        )
        result = await _handle_model_add(mh, _ctx(), params)
        assert result.model == ["anthropic", "sonnet"]
        assert result.display_name == "Sonnet"
        assert result.roles == ["default"]


class TestHandleModelUpdate:
    async def test_delegates(self) -> None:
        mh = MagicMock()
        mh.update_model = AsyncMock(
            return_value=UpdateModelResult(
                model=["anthropic", "sonnet"],
                provider_type="anthropic",
                base_url=None,
                effective_base_url=None,
                aws_region=None,
                has_api_key=True,
                api_key_display="sk-anthropic-1234",
                has_aws_secret_key=False,
                aws_secret_key_display=None,
                setting_fields=["api_key", "base_url"],
                display_name="Sonnet",
                context_window=200_000,
                roles=["default"],
            ),
        )
        params = UpdateModelRequest(
            provider="anthropic",
            model="sonnet",
            displayName="Sonnet",
            contextWindow=200_000,
            roles=["default"],
        )
        result = await _handle_model_update(mh, _ctx(), params)
        assert result.model == ["anthropic", "sonnet"]
        assert result.provider_type == "anthropic"
        assert result.has_api_key is True
        assert result.api_key_display == "sk-anthropic-1234"
        assert result.setting_fields == ["api_key", "base_url"]
        assert result.display_name == "Sonnet"
        assert result.context_window == 200_000
        assert result.roles == ["default"]
