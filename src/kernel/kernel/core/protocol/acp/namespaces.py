"""ACP method namespace constants and classification helpers.

This module is the single place that names Mustang's ACP protocol surface.
Official method names come from the mirrored ACP schema; Mustang-owned names
must be extension-prefixed. Batch G removed the temporary unprefixed legacy
aliases.
"""

from __future__ import annotations

from enum import StrEnum


MUSTANG_EXTENSION_PREFIX = "_mustang.agent/"
MUSTANG_META_PREFIX = "mustang.agent/"


class MethodKind(StrEnum):
    STANDARD = "standard"
    MUSTANG_EXTENSION = "mustang_extension"
    CLIENT_METHOD = "client_method"
    UNSUPPORTED_OFFICIAL = "unsupported_official"


class AcpMethod:
    INITIALIZE = "initialize"
    AUTHENTICATE = "authenticate"
    SESSION_NEW = "session/new"
    SESSION_LOAD = "session/load"
    SESSION_LIST = "session/list"
    SESSION_PROMPT = "session/prompt"
    SESSION_CANCEL = "session/cancel"
    SESSION_CLOSE = "session/close"
    SESSION_RESUME = "session/resume"
    SESSION_SET_MODE = "session/set_mode"
    SESSION_SET_CONFIG_OPTION = "session/set_config_option"
    SESSION_UPDATE = "session/update"
    SESSION_REQUEST_PERMISSION = "session/request_permission"
    FS_READ_TEXT_FILE = "fs/read_text_file"
    FS_WRITE_TEXT_FILE = "fs/write_text_file"
    TERMINAL_CREATE = "terminal/create"
    TERMINAL_OUTPUT = "terminal/output"
    TERMINAL_WAIT_FOR_EXIT = "terminal/wait_for_exit"
    TERMINAL_KILL = "terminal/kill"
    TERMINAL_RELEASE = "terminal/release"


class MustangMethod:
    COMMANDS_LIST = "_mustang.agent/commands/list"
    SESSION_ACTIVATE_SKILL = "_mustang.agent/session/activate_skill"
    SESSION_EXECUTE_SHELL = "_mustang.agent/session/execute_shell"
    SESSION_EXECUTE_PYTHON = "_mustang.agent/session/execute_python"
    SESSION_CANCEL_EXECUTION = "_mustang.agent/session/cancel_execution"
    SESSION_EXECUTION_UPDATE = "_mustang.agent/session/execution_update"
    SESSION_RENAME = "_mustang.agent/session/rename"
    SESSION_ARCHIVE = "_mustang.agent/session/archive"
    SESSION_DELETE = "_mustang.agent/session/delete"
    SESSION_GET_USAGE = "_mustang.agent/session/get_usage"
    SESSION_TOOL_SNAPSHOT = "_mustang.agent/session/tool_snapshot"
    MODEL_PROFILE_LIST = "_mustang.agent/model/profile_list"
    MODEL_PROVIDER_LIST = "_mustang.agent/model/provider_list"
    MODEL_PROVIDER_ADD = "_mustang.agent/model/provider_add"
    MODEL_PROVIDER_REMOVE = "_mustang.agent/model/provider_remove"
    MODEL_PROVIDER_REFRESH = "_mustang.agent/model/provider_refresh"
    MODEL_SET_CURRENT = "_mustang.agent/model/set_current"
    MODEL_ADD = "_mustang.agent/model/add"
    MODEL_UPDATE = "_mustang.agent/model/update"
    WEB_FETCH_BACKEND_OPTIONS = "_mustang.agent/web_fetch/backend_options"
    WEB_FETCH_SET_BACKEND = "_mustang.agent/web_fetch/set_backend"
    WEB_FETCH_GET_CONFIG = "_mustang.agent/web_fetch/get_config"
    WEB_FETCH_SET_CONFIG = "_mustang.agent/web_fetch/set_config"
    SECRETS_AUTH = "_mustang.agent/secrets/auth"
    RUNTIME_STATUS = "_mustang.agent/runtime/status"
    RUNTIME_RESTART = "_mustang.agent/runtime/restart"


MUSTANG_EXTENSION_METHODS = frozenset(
    {
        MustangMethod.COMMANDS_LIST,
        MustangMethod.SESSION_ACTIVATE_SKILL,
        MustangMethod.SESSION_EXECUTE_SHELL,
        MustangMethod.SESSION_EXECUTE_PYTHON,
        MustangMethod.SESSION_CANCEL_EXECUTION,
        MustangMethod.SESSION_EXECUTION_UPDATE,
        MustangMethod.SESSION_RENAME,
        MustangMethod.SESSION_ARCHIVE,
        MustangMethod.SESSION_DELETE,
        MustangMethod.SESSION_GET_USAGE,
        MustangMethod.SESSION_TOOL_SNAPSHOT,
        MustangMethod.MODEL_PROFILE_LIST,
        MustangMethod.MODEL_PROVIDER_LIST,
        MustangMethod.MODEL_PROVIDER_ADD,
        MustangMethod.MODEL_PROVIDER_REMOVE,
        MustangMethod.MODEL_PROVIDER_REFRESH,
        MustangMethod.MODEL_SET_CURRENT,
        MustangMethod.MODEL_ADD,
        MustangMethod.MODEL_UPDATE,
        MustangMethod.WEB_FETCH_BACKEND_OPTIONS,
        MustangMethod.WEB_FETCH_SET_BACKEND,
        MustangMethod.WEB_FETCH_GET_CONFIG,
        MustangMethod.WEB_FETCH_SET_CONFIG,
        MustangMethod.SECRETS_AUTH,
        MustangMethod.RUNTIME_STATUS,
        MustangMethod.RUNTIME_RESTART,
    }
)

CLIENT_METHODS = frozenset(
    {
        AcpMethod.SESSION_UPDATE,
        AcpMethod.SESSION_REQUEST_PERMISSION,
        AcpMethod.FS_READ_TEXT_FILE,
        AcpMethod.FS_WRITE_TEXT_FILE,
        AcpMethod.TERMINAL_CREATE,
        AcpMethod.TERMINAL_OUTPUT,
        AcpMethod.TERMINAL_WAIT_FOR_EXIT,
        AcpMethod.TERMINAL_KILL,
        AcpMethod.TERMINAL_RELEASE,
    }
)


def classify_method(method: str, official_methods: set[str]) -> MethodKind:
    if method in official_methods:
        if method in CLIENT_METHODS:
            return MethodKind.CLIENT_METHOD
        return MethodKind.STANDARD
    if method.startswith(MUSTANG_EXTENSION_PREFIX):
        return MethodKind.MUSTANG_EXTENSION
    return MethodKind.UNSUPPORTED_OFFICIAL
