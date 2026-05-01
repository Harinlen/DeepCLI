"""ConfigTool — read and write Mustang configuration.

Exposes the 3-layer config system (user / project / local) to the LLM
so it can inspect current settings and make targeted changes on behalf
of the user (e.g. adding a permission rule, tweaking a tool setting).

Security boundary: project/local layers cannot contain ``provider``,
``daemon``, or ``default_provider`` fields.  Writes to those fields
are only allowed in the ``user`` layer.  API keys are redacted on read.
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from daemon.config.loader import CONFIG_PATH
from daemon.config.project import (
    LOCAL_CONFIG_NAME,
    PROJECT_CONFIG_NAME,
    PROJECT_DIR_NAME,
    _DISALLOWED_FIELDS,
    ensure_local_gitignored,
    find_project_root,
)
from daemon.config.schema import RuntimeConfig
from daemon.extensions.tools.base import (
    ConcurrencyHint,
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)

logger = logging.getLogger(__name__)

# Keys that contain secrets — redacted on read.
_SECRET_KEYS = frozenset({"api_key"})


class ConfigTool(Tool):
    """Read and write Mustang configuration settings."""

    name = "config_tool"
    description = (
        "Read or write Mustang configuration. Supports 3 layers: "
        '"user" (~/.mustang/config.yaml — global settings, provider keys), '
        '"project" (<root>/.mustang/settings.json — git-tracked team settings), '
        '"local" (<root>/.mustang/settings.local.json — personal overrides, gitignored). '
        "Use layer='user' for provider/daemon settings. "
        "Use layer='project' for permissions, hooks, MCP servers shared with the team. "
        "Use layer='local' for personal overrides that should not be committed. "
        "NEVER write provider or daemon settings to project/local layers — security boundary."
    )
    permission_level = PermissionLevel.PROMPT
    concurrency = ConcurrencyHint.SERIAL

    class Input(BaseModel):
        action: Literal["read", "write"] = Field(
            description="'read' to inspect config, 'write' to update a value.",
        )
        key_path: str | None = Field(
            default=None,
            description=(
                "Dot-separated path to a config key (e.g. 'tools.bash.timeout', "
                "'permissions.mode', 'hooks'). Omit to show the full config."
            ),
        )
        value: Any = Field(
            default=None,
            description="Value to set (required for 'write'). Use JSON types.",
        )
        layer: Literal["user", "project", "local"] = Field(
            default="project",
            description=(
                "Config layer to read from or write to. "
                "'user' = ~/.mustang/config.yaml (global), "
                "'project' = .mustang/settings.json (git-tracked), "
                "'local' = .mustang/settings.local.json (gitignored). "
                "For 'read' with layer='resolved', shows the merged result."
            ),
        )

    def __init__(self, config: RuntimeConfig) -> None:
        self._config = config

    def get_permission_level(self, params: dict[str, Any]) -> PermissionLevel:
        if params.get("action") == "read":
            return PermissionLevel.NONE
        return PermissionLevel.PROMPT

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        validated = self.Input.model_validate(params)
        if validated.action == "read":
            return self._read(validated, ctx)
        return self._write(validated, ctx)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def _read(self, inp: Input, ctx: ToolContext) -> ToolResult:
        data = self._load_layer(inp.layer, ctx)
        if isinstance(data, ToolResult):
            return data  # error

        if inp.key_path:
            value = _get_nested(data, inp.key_path)
            if value is _MISSING:
                return ToolResult(
                    output=f"Key '{inp.key_path}' not found in {inp.layer} config.",
                    is_error=True,
                )
            data = value

        redacted = _redact_secrets(data)
        formatted = _format_value(redacted)
        return ToolResult(output=formatted)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def _write(self, inp: Input, ctx: ToolContext) -> ToolResult:
        if not inp.key_path:
            return ToolResult(
                output="key_path is required for 'write' action.",
                is_error=True,
            )
        if inp.value is None:
            return ToolResult(
                output="value is required for 'write' action. Use value=null to delete a key.",
                is_error=True,
            )

        top_key = inp.key_path.split(".")[0]

        # Security boundary: project/local cannot contain provider/daemon.
        if inp.layer in ("project", "local") and top_key in _DISALLOWED_FIELDS:
            return ToolResult(
                output=(
                    f"Cannot write '{top_key}' to {inp.layer} config — "
                    f"security boundary. Use layer='user' for provider/daemon settings."
                ),
                is_error=True,
            )

        # Resolve file path.
        path = self._resolve_write_path(inp.layer, ctx)
        if isinstance(path, ToolResult):
            return path  # error

        # Load existing content.
        data = self._load_file(path)

        # Set the value at key_path.
        _set_nested(data, inp.key_path, inp.value)

        # Write back.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".yaml":
                path.write_text(
                    yaml.dump(data, default_flow_style=False, allow_unicode=True)
                )
            else:
                path.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False) + "\n"
                )
        except OSError as exc:
            return ToolResult(output=f"Failed to write config: {exc}", is_error=True)

        # Auto-gitignore local config.
        if inp.layer == "local":
            cwd = Path(ctx.cwd)
            root = find_project_root(cwd)
            if root:
                ensure_local_gitignored(root)

        logger.info("Config updated: %s = %r in %s", inp.key_path, inp.value, path)
        return ToolResult(
            output=f"Set {inp.key_path} = {json.dumps(inp.value)} in {inp.layer} config ({path})."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_layer(self, layer: str, ctx: ToolContext) -> dict | ToolResult:
        """Load raw config dict for a given layer."""
        if layer == "user":
            return self._load_file(CONFIG_PATH)

        cwd = Path(ctx.cwd)
        root = find_project_root(cwd)
        if root is None:
            return ToolResult(
                output=f"No .mustang/ directory found from {cwd}. Cannot read {layer} config.",
                is_error=True,
            )

        if layer == "project":
            return self._load_file(root / PROJECT_DIR_NAME / PROJECT_CONFIG_NAME)
        if layer == "local":
            return self._load_file(root / PROJECT_DIR_NAME / LOCAL_CONFIG_NAME)

        return ToolResult(output=f"Unknown layer: {layer}", is_error=True)

    def _resolve_write_path(self, layer: str, ctx: ToolContext) -> Path | ToolResult:
        """Resolve the file path for a write operation."""
        if layer == "user":
            return CONFIG_PATH

        cwd = Path(ctx.cwd)
        root = find_project_root(cwd)
        if root is None:
            return ToolResult(
                output=f"No .mustang/ directory found from {cwd}. Cannot write {layer} config.",
                is_error=True,
            )

        if layer == "project":
            return root / PROJECT_DIR_NAME / PROJECT_CONFIG_NAME
        return root / PROJECT_DIR_NAME / LOCAL_CONFIG_NAME

    @staticmethod
    def _load_file(path: Path) -> dict:
        """Load a config file, returning empty dict if missing."""
        if not path.is_file():
            return {}
        try:
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".yaml":
                return yaml.safe_load(text) or {}
            return json.loads(text) if text.strip() else {}
        except (json.JSONDecodeError, yaml.YAMLError, OSError) as exc:
            logger.warning("Cannot load config at %s: %s", path, exc)
            return {}


# ======================================================================
# Pure helpers
# ======================================================================

_MISSING = object()


def _get_nested(data: Any, key_path: str) -> Any:
    """Traverse a nested dict by dot-separated path."""
    current = data
    for part in key_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def _set_nested(data: dict, key_path: str, value: Any) -> None:
    """Set a value in a nested dict by dot-separated path, creating intermediates."""
    parts = key_path.split(".")
    current = data
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def _redact_secrets(obj: Any) -> Any:
    """Recursively redact secret fields."""
    if isinstance(obj, dict):
        return {
            k: "***REDACTED***" if k in _SECRET_KEYS and isinstance(v, str) else _redact_secrets(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_secrets(item) for item in obj]
    return obj


def _format_value(value: Any) -> str:
    """Format a value for display."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    return str(value)
