"""Load and update configuration from ~/.mustang/config.yaml.

Provides ``load_config()`` for reading and ``update_provider_field()``
for writing individual provider fields back to the YAML file.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from daemon.config.defaults import apply_defaults
from daemon.config.schema import RuntimeConfig, SourceConfig
from daemon.errors import ConfigError

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".mustang"
CONFIG_PATH = CONFIG_DIR / "config.yaml"

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _substitute_env_vars(value: str) -> str:
    """Replace ${VAR} references with environment variable values."""

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        val = os.environ.get(var_name)
        if val is None:
            logger.warning(
                "Config references undefined environment variable ${%s} — "
                "substituting empty string",
                var_name,
            )
            return ""
        return val

    return _ENV_VAR_RE.sub(_replace, value)


def _walk_and_substitute(obj: object) -> object:
    """Recursively substitute env vars in all string values."""
    if isinstance(obj, str):
        return _substitute_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _walk_and_substitute(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_and_substitute(item) for item in obj]
    return obj


def load_config(
    path: Path | None = None,
    cwd: Path | None = None,
) -> RuntimeConfig:
    """Load config from YAML file, apply env substitution and defaults.

    When *cwd* is provided, also looks for project-level and local
    settings (``<root>/.mustang/settings.json`` and
    ``<root>/.mustang/settings.local.json``) and merges them on top
    of the user config (local > project > user).

    If the config file doesn't exist, returns pure defaults (zero-config).

    Args:
        path: Override config file path. Defaults to ``~/.mustang/config.yaml``.
        cwd: Working directory for project config discovery.

    Returns:
        Fully resolved RuntimeConfig ready for use.

    Raises:
        ConfigError: If the YAML is malformed or fails Pydantic validation.
    """
    config_path = path or CONFIG_PATH

    try:
        if config_path.exists():
            raw: dict = yaml.safe_load(config_path.read_text()) or {}
            raw = _walk_and_substitute(raw)  # type: ignore[assignment]
        else:
            raw = {}

        # Merge project + local overrides when cwd is given.
        if cwd is not None:
            from daemon.config.project import (
                find_project_root,
                load_project_settings,
                merge_configs,
            )

            root = find_project_root(cwd)
            if root is not None:
                project, local = load_project_settings(root)
                if project or local:
                    logger.info("Loaded project config from %s/.mustang/", root)
                    raw = merge_configs(raw, project, local)

        source = SourceConfig.model_validate(raw)
        return apply_defaults(source)
    except ValidationError as e:
        raise ConfigError(f"Invalid config at {config_path}: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse YAML at {config_path}: {e}") from e


def update_provider_field(
    provider_name: str,
    field: str,
    value: object,
    path: Path | None = None,
) -> bool:
    """Write a single field into a provider's config in the YAML file.

    Reads the file, sets ``provider.<name>.<field>`` to *value*, and
    writes it back.  Preserves existing content as much as possible
    (PyYAML round-trip does not preserve comments, but the structure
    is kept).

    Skips the write if the field already has the same value.

    Args:
        provider_name: The provider key (e.g. ``"minimax"``).
        field: The field to set (e.g. ``"context_window"``).
        value: The value to write.
        path: Override config file path.

    Returns:
        ``True`` if the file was updated, ``False`` if skipped.
    """
    config_path = path or CONFIG_PATH

    try:
        raw: dict = {}
        if config_path.exists():
            raw = yaml.safe_load(config_path.read_text()) or {}

        providers = raw.setdefault("provider", {})
        if not isinstance(providers, dict):
            logger.warning("Cannot update config: 'provider' is not a mapping")
            return False

        provider_section = providers.get(provider_name)
        if provider_section is None or isinstance(provider_section, str):
            # Provider not present or is a bare string reference — skip
            logger.debug("Provider %r not found in config, skipping field update", provider_name)
            return False

        # Skip if already set to the same value
        if provider_section.get(field) == value:
            return False

        provider_section[field] = value

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.dump(raw, default_flow_style=False, allow_unicode=True))
        logger.info(
            "Auto-saved provider.%s.%s = %s to %s",
            provider_name,
            field,
            value,
            config_path,
        )
        return True

    except Exception:
        logger.warning(
            "Failed to auto-save provider.%s.%s to config",
            provider_name,
            field,
            exc_info=True,
        )
        return False
