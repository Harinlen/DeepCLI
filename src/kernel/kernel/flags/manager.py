from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from kernel.flags.kernel_flags import KernelFlags
from kernel.paths import user_config_dir, user_path

logger = logging.getLogger(__name__)

def _resolve_default_path() -> Path:
    """Return the flags file path, with env-var override for testing.

    ``DEEPCLI_FLAGS_PATH`` lets tests point the kernel at a temporary
    flags file without touching ``~/.deepcli/config/flags.yaml``.
    ``MUSTANG_FLAGS_PATH`` is retained as a legacy alias.
    """
    import os

    env = os.environ.get("DEEPCLI_FLAGS_PATH")
    if env:
        return Path(env)
    env = os.environ.get("MUSTANG_FLAGS_PATH")
    if env:
        return Path(env)
    return user_config_dir() / "flags.yaml"


_DEFAULT_PATH = _resolve_default_path()

T = TypeVar("T", bound=BaseModel)


class FlagManager:
    """Owns ``~/.deepcli/config/flags.yaml`` and serves typed flag sections.

    Runtime contract: flags are **frozen at startup**.  Subsystems
    read ``~/.deepcli/config/flags.yaml`` once during :meth:`initialize`,
    register their section schemas in their own ``startup``, and use
    the returned Pydantic instances directly.  There is no runtime
    mutation, no hot reload, and no write path — changing a flag
    means editing ``flags.yaml`` and restarting the kernel.

    This "boot-time decision, runtime freeze" is why :meth:`register`
    returns the schema instance directly instead of a callable
    accessor.  Runtime-mutable configuration lives in
    :class:`kernel.config.manager.ConfigManager`, which owns a
    separate ``bind_section`` / signal-based interface.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path: Path = path if path is not None else _DEFAULT_PATH
        self._raw: dict[str, Any] = {}
        self._schemas: dict[str, type[BaseModel]] = {}
        self._instances: dict[str, BaseModel] = {}
        self._initialized = False

    async def initialize(self) -> None:
        """Load ``flags.yaml`` (if present) and register built-in sections.

        If the file does not exist, all sections fall back to their
        schema defaults and nothing is written to disk.
        """
        if self._initialized:
            return

        self._raw = self._load_raw(self._read_path())
        self.register("kernel", KernelFlags)
        self._initialized = True

    def register(self, section: str, schema: type[T]) -> T:
        """Register a section schema and return its frozen instance.

        The caller may cache the returned value for the lifetime of
        the kernel — FlagManager guarantees it will not mutate any
        section after startup.

        Raises
        ------
        ValueError
            If ``section`` is already registered, or the raw YAML
            entry for this section is not a mapping.
        pydantic.ValidationError
            If the raw YAML data for this section fails validation.
        """
        if section in self._schemas:
            raise ValueError(f"Flag section already registered: {section!r}")

        raw_section = self._raw.get(section) or {}
        if not isinstance(raw_section, dict):
            raise ValueError(
                f"Flag section {section!r} in {self._path} must be a mapping, "
                f"got {type(raw_section).__name__}"
            )

        instance = schema.model_validate(raw_section)
        self._schemas[section] = schema
        self._instances[section] = instance
        return instance

    def get_section(self, section: str) -> BaseModel:
        """Return the frozen Pydantic instance for a registered section."""
        try:
            return self._instances[section]
        except KeyError as exc:
            raise KeyError(f"Flag section not registered: {section!r}") from exc

    def list_all(self) -> dict[str, tuple[type[BaseModel], BaseModel]]:
        """Return every registered section as ``(schema, instance)`` pairs.

        Intended for rendering settings UIs: the schema provides field
        metadata (types, defaults, descriptions) and the instance
        carries the current values.  The UI can only prompt the user
        to edit ``flags.yaml`` and restart — there is no write path.
        """
        return {name: (self._schemas[name], self._instances[name]) for name in self._schemas}

    def _read_path(self) -> Path:
        """Return canonical path, with read-only fallback for old installs."""
        if self._path.exists() or self._path != _DEFAULT_PATH:
            return self._path
        legacy_path = user_path("flags.yaml")
        if legacy_path.exists():
            logger.warning(
                "Loaded legacy flags from %s; move this file to %s",
                legacy_path,
                self._path,
            )
            return legacy_path
        return self._path

    @staticmethod
    def _load_raw(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError(
                f"{path} must contain a YAML mapping at the top level, got {type(data).__name__}"
            )
        return data
