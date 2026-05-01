"""Permission system — rule engine, modes, parsing, and persistence."""

from daemon.permissions.engine import PermissionDecision, PermissionEngine
from daemon.permissions.modes import PermissionMode, is_read_only_tool
from daemon.permissions.policy import needs_permission
from daemon.permissions.rules import (
    PermissionRule,
    ToolRule,
    matches,
    parse_rule,
)
from daemon.permissions.settings import DEFAULT_SETTINGS_PATH, PermissionSettings

__all__ = [
    "DEFAULT_SETTINGS_PATH",
    "PermissionDecision",
    "PermissionEngine",
    "PermissionMode",
    "PermissionRule",
    "PermissionSettings",
    "ToolRule",
    "is_read_only_tool",
    "matches",
    "needs_permission",
    "parse_rule",
]
