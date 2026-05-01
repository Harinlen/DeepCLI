"""Tests for ACP routing handler wrappers.

Each _handle_* function converts ACP wire-format params to internal
contract types and forwards to the appropriate handler. Tests verify
the conversion and delegation logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


from kernel.protocol.acp.namespaces import (
    MUSTANG_EXTENSION_PREFIX,
    AcpMethod,
    MethodKind,
    MustangMethod,
    classify_method,
)
from kernel.protocol.acp.routing import (
    REQUEST_DISPATCH,
    NOTIFICATION_DISPATCH,
    _handle_archive_session,
    _handle_close,
    _handle_cancel_execution,
    _handle_delete_session,
    _handle_execute_python,
    _handle_execute_shell,
    _handle_new,
    _handle_load,
    _handle_list,
    _handle_prompt,
    _handle_set_mode,
    _handle_set_config_option,
    _handle_rename_session,
    _handle_resume,
    _handle_cancel,
    _handle_provider_list,
    _handle_provider_add,
    _handle_provider_remove,
    _handle_provider_refresh,
    _handle_set_default,
)
from kernel.protocol.acp.schemas.model import (
    AddProviderRequest,
    ListProvidersRequest,
    RefreshModelsRequest,
    RemoveProviderRequest,
    SetDefaultModelRequest,
)
from kernel.protocol.acp.schemas.session import (
    ArchiveSessionRequest,
    CancelExecutionRequest,
    CancelNotification,
    CloseSessionRequest,
    DeleteSessionRequest,
    ExecutePythonRequest,
    ExecuteShellRequest,
    ListSessionsRequest,
    LoadSessionRequest,
    NewSessionRequest,
    PromptRequest,
    RenameSessionRequest,
    ResumeSessionRequest,
    SetSessionConfigOptionRequest,
    SetSessionModeRequest,
)
from kernel.protocol.interfaces.contracts.archive_session_result import ArchiveSessionResult
from kernel.protocol.interfaces.contracts.close_session_result import CloseSessionResult
from kernel.protocol.interfaces.contracts.delete_session_result import DeleteSessionResult
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.protocol.interfaces.contracts.execution_result import ExecutionResult
from kernel.protocol.interfaces.contracts.list_providers_result import (
    ListProvidersResult,
    ProviderInfo,
)
from kernel.protocol.interfaces.contracts.new_session_result import NewSessionResult
from kernel.protocol.interfaces.contracts.load_session_result import LoadSessionResult
from kernel.protocol.interfaces.contracts.list_sessions_result import ListSessionsResult
from kernel.protocol.interfaces.contracts.rename_session_result import RenameSessionResult
from kernel.protocol.interfaces.contracts.resume_session_result import ResumeSessionResult
from kernel.protocol.interfaces.contracts.set_mode_result import SetModeResult
from kernel.protocol.interfaces.contracts.set_config_option_result import (
    SetConfigOptionResult,
)
from kernel.protocol.interfaces.contracts.add_provider_result import AddProviderResult
from kernel.protocol.interfaces.contracts.remove_provider_result import RemoveProviderResult
from kernel.protocol.interfaces.contracts.refresh_models_result import RefreshModelsResult
from kernel.protocol.interfaces.contracts.set_default_model_result import (
    SetDefaultModelResult,
)


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
        ]:
            assert method in REQUEST_DISPATCH

    def test_all_model_methods_present(self) -> None:
        for method in [
            MustangMethod.MODEL_PROFILE_LIST,
            MustangMethod.MODEL_PROVIDER_LIST,
            MustangMethod.MODEL_PROVIDER_ADD,
            MustangMethod.MODEL_PROVIDER_REMOVE,
            MustangMethod.MODEL_PROVIDER_REFRESH,
            MustangMethod.MODEL_SET_DEFAULT,
        ]:
            assert method in REQUEST_DISPATCH

    def test_cancel_notification(self) -> None:
        assert AcpMethod.SESSION_CANCEL in NOTIFICATION_DISPATCH
        assert MustangMethod.SESSION_CANCEL_EXECUTION in NOTIFICATION_DISPATCH

    def test_session_targets(self) -> None:
        for method in [AcpMethod.SESSION_NEW, AcpMethod.SESSION_LOAD, AcpMethod.SESSION_LIST]:
            assert REQUEST_DISPATCH[method].target == "session"

    def test_model_targets(self) -> None:
        for method in [MustangMethod.MODEL_PROVIDER_LIST, MustangMethod.MODEL_PROVIDER_ADD]:
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
        from kernel.protocol.interfaces.contracts.list_sessions_result import SessionSummary

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
        from kernel.protocol.interfaces.contracts.list_sessions_result import SessionSummary

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
        from kernel.protocol.interfaces.contracts.prompt_result import PromptResult

        sh = MagicMock()
        sh.prompt = AsyncMock(
            return_value=PromptResult(
                stop_reason="end_turn",
                meta={
                    "mustang.agent/clientTurnId": "11111111-1111-4111-8111-111111111111"
                },
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
        from kernel.protocol.interfaces.contracts.session_config import ConfigOptionDescriptor

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


class TestHandleProviderList:
    async def test_converts_providers(self) -> None:
        mh = MagicMock()
        mh.list_providers = AsyncMock(
            return_value=ListProvidersResult(
                providers=[
                    ProviderInfo(
                        name="anthropic",
                        provider_type="anthropic",
                        models=["claude-opus-4-6"],
                        roles={"default": True},
                    ),
                ],
                default_model=["anthropic", "claude-opus-4-6"],
            )
        )
        result = await _handle_provider_list(mh, _ctx(), ListProvidersRequest())
        assert len(result.providers) == 1
        assert result.providers[0].name == "anthropic"
        assert result.default_model == ["anthropic", "claude-opus-4-6"]


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


class TestHandleSetDefault:
    async def test_delegates(self) -> None:
        mh = MagicMock()
        mh.set_default_model = AsyncMock(
            return_value=SetDefaultModelResult(default_model=["anthropic", "sonnet"]),
        )
        params = SetDefaultModelRequest(provider="anthropic", model="sonnet")
        result = await _handle_set_default(mh, _ctx(), params)
        assert result.default_model == ["anthropic", "sonnet"]
