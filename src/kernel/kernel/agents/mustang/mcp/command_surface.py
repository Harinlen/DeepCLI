"""Kernel-owned MCP declaration management facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from kernel.agents.mustang.mcp.config import (
    MCPConfig,
    MCPDeclarationRecord,
    MCPDeclarationStore,
    ServerConfig,
)

_SERVER_CONFIG_ADAPTER: TypeAdapter[Any] = TypeAdapter(ServerConfig)
_SENSITIVE_KEYS = ("authorization", "api_key", "apikey", "token", "secret", "password", "key")
_REDACTED = "<redacted>"


class MCPCommandService:
    """Management facade for ResourceStore-backed global MCP declarations.

    The service mutates only the MCP global declaration row in ResourceStore
    through :class:`MCPDeclarationStore`. Runtime connection/session state stays
    owned by :class:`MCPManager` and is intentionally not managed here.
    """

    def __init__(self, home: Path, *, config_manager: Any | None = None) -> None:
        self._home = home
        self._config_manager = config_manager

    def list(self) -> dict[str, Any]:
        """Return global MCP declarations without plaintext credential values."""
        record = self._read()
        if record is None:
            return {"servers": [], "revision": 0}
        return {
            "servers": [
                {
                    "name": name,
                    "type": cfg.type,
                    "config": _safe_server_config(cfg),
                }
                for name, cfg in sorted(record.config.servers.items())
            ],
            "revision": record.revision,
        }

    def read(self, name: str) -> dict[str, Any]:
        """Read one MCP server declaration by name."""
        record = self._read()
        if record is None or name not in record.config.servers:
            raise KeyError(f"unknown MCP server: {name}")
        cfg = record.config.servers[name]
        return {
            "server": {
                "name": name,
                "type": cfg.type,
                "config": _safe_server_config(cfg),
            },
            "revision": record.revision,
        }

    def create(
        self,
        name: str,
        config: dict[str, Any],
        *,
        expected_revision: int | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Create a new global MCP declaration and bump its revision."""
        current = self._read()
        servers = dict(current.config.servers) if current is not None else {}
        if name in servers:
            raise ValueError(f"MCP server already exists: {name}")
        parsed = _parse_server_config(config)
        _reject_plaintext_credentials(parsed)
        servers[name] = parsed
        record = self._write(
            MCPConfig(servers=servers),
            expected_revision=expected_revision
            if current is None
            else _expected(expected_revision, current),
            actor=actor,
        )
        return self.read_from_record(record, name)

    def update(
        self,
        name: str,
        config: dict[str, Any],
        *,
        expected_revision: int | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Replace one global MCP declaration and bump its revision."""
        current = self._read()
        if current is None or name not in current.config.servers:
            raise KeyError(f"unknown MCP server: {name}")
        parsed = _parse_server_config(config)
        _reject_plaintext_credentials(parsed)
        servers = dict(current.config.servers)
        servers[name] = parsed
        record = self._write(
            MCPConfig(servers=servers),
            expected_revision=_expected(expected_revision, current),
            actor=actor,
        )
        return self.read_from_record(record, name)

    def delete(
        self,
        name: str,
        *,
        expected_revision: int | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Delete one global MCP declaration and bump its revision."""
        current = self._read()
        if current is None or name not in current.config.servers:
            raise KeyError(f"unknown MCP server: {name}")
        servers = dict(current.config.servers)
        servers.pop(name)
        record = self._write(
            MCPConfig(servers=servers),
            expected_revision=_expected(expected_revision, current),
            actor=actor,
        )
        return {
            "name": name,
            "deleted": True,
            "revision": record.revision,
            "applies": "after_restart",
            "pending_restart": True,
        }

    def read_from_record(self, record: MCPDeclarationRecord, name: str) -> dict[str, Any]:
        """Build a single-server response from a known post-write record."""
        cfg = record.config.servers[name]
        return {
            "server": {
                "name": name,
                "type": cfg.type,
                "config": _safe_server_config(cfg),
            },
            "revision": record.revision,
            "applies": "after_restart",
            "pending_restart": True,
        }

    def _read(self) -> MCPDeclarationRecord | None:
        store = MCPDeclarationStore.open(self._home)
        try:
            return store.read_global()
        finally:
            store.close()

    def _write(
        self,
        config: MCPConfig,
        *,
        expected_revision: int | None,
        actor: str | None,
    ) -> MCPDeclarationRecord:
        store = MCPDeclarationStore.open(self._home)
        try:
            record = store.write_global(
                config,
                expected_revision=expected_revision,
                actor=actor,
            )
        finally:
            store.close()
        self._refresh_config_manager()
        return record

    def _refresh_config_manager(self) -> None:
        refresh = getattr(self._config_manager, "refresh_from_resource_store", None)
        if callable(refresh):
            refresh()


def _expected(explicit: int | None, current: MCPDeclarationRecord) -> int:
    return current.revision if explicit is None else explicit


def _parse_server_config(config: dict[str, Any]) -> ServerConfig:
    if "type" not in config and "command" in config:
        config = {"type": "stdio", **config}
    return _SERVER_CONFIG_ADAPTER.validate_python(config)


def _safe_server_config(config: ServerConfig) -> dict[str, Any]:
    data = config.model_dump(mode="json")
    for field in ("env", "headers"):
        values = data.get(field)
        if isinstance(values, dict):
            data[field] = {key: _safe_value(key, value) for key, value in values.items()}
    return data


def _reject_plaintext_credentials(config: ServerConfig) -> None:
    data = config.model_dump(mode="json")
    for field in ("env", "headers"):
        values = data.get(field)
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if _is_sensitive_key(key) and _looks_plaintext_secret(str(value)):
                raise ValueError(
                    f"MCP {field}.{key} must use a secret:<uuid> ref or environment reference"
                )


def _safe_value(key: str, value: Any) -> Any:
    text = str(value)
    if text.startswith("secret:") or text.startswith("$"):
        return text
    if _is_sensitive_key(key):
        return _REDACTED
    return value


def _is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return any(marker in lower for marker in _SENSITIVE_KEYS)


def _looks_plaintext_secret(value: str) -> bool:
    return not (value.startswith("secret:") or value.startswith("$"))
