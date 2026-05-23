"""Tools subsystem — built-in tool registry.

Public surface:

- :class:`ToolManager` — Subsystem loaded at step 5 of kernel lifespan,
  owns the :class:`ToolRegistry` + :class:`FileStateCache` and registers
  the built-in tools gated by :class:`ToolFlags`.
- :class:`Tool` — the ABC every built-in / MCP / user tool inherits from.
- :class:`ToolContext` — the single channel through which a Tool touches
  the rest of the kernel.
- :class:`ToolRegistry` / :class:`ToolSnapshot` — registry type + per-turn
  snapshot used by Orchestrator.
- :class:`FileStateCache` — shared state between file-reading and
  file-editing tools.
- Tool-facing types: :class:`PermissionSuggestion`, :class:`ToolCallResult`,
  :class:`ToolCallProgress`, :class:`ToolInputError`, the
  :class:`ToolDisplayPayload` union.

See ``docs/plans/landed/tool-manager.md`` for the full design.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, cast

import httpx

from kernel.core.lifecycle import Subsystem
from kernel.agents.mustang.tools.builtin import BUILTIN_TOOLS
from kernel.agents.mustang.tools.context import ToolContext
from kernel.agents.mustang.tools.file_state import FileState, FileStateCache, hash_text
from kernel.agents.mustang.tools.flags import ToolFlags
from kernel.agents.mustang.tools.matching import matches_name
from kernel.agents.mustang.tools.registry import Layer, ToolRegistry, ToolSnapshot
from kernel.agents.mustang.tools.tool import Tool
from kernel.agents.mustang.tools.types import (
    DiffDisplay,
    FileDisplay,
    LocationsDisplay,
    PermissionSuggestion,
    RawBlocks,
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
    ToolDisplayPayload,
    ToolInputError,
)
from kernel.agents.mustang.tools.web.config import WebFetchConfig
from kernel.agents.mustang.tools.web.management import (
    backend_ids,
    backend_is_available,
    backend_is_installed,
    build_backend_options,
    build_setup_plan,
    credential_request,
    get_definition,
    primary_api_key_env,
    run_setup,
)

if TYPE_CHECKING:
    from kernel.agents.mustang.module_table import KernelModuleTable

logger = logging.getLogger(__name__)


class ToolManager(Subsystem):
    """Tools subsystem — registry + shared state provider.

    Responsibilities:

    - Register all enabled built-in tools at startup.
    - Own the single ``FileStateCache`` shared across file-* tools.
    - Expose ``snapshot_for_session`` for Orchestrator; filters by
      plan-mode, sub-agent whitelist, and the ToolAuthorizer's
      ``filter_denied_tools`` deny-list.
    - Hand the ``FileStateCache`` out to the Session layer so Orchestrator
      can construct a ``ToolContext`` for each turn.
    """

    def __init__(self, module_table: KernelModuleTable) -> None:
        super().__init__(module_table)
        self._flags: ToolFlags | None = None
        self._web_fetch_config: Any = None
        self._web_bridge: Any = None
        self._registry = ToolRegistry()
        self._file_state = FileStateCache()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Register FlagManager section + instantiate enabled built-ins."""
        flag_manager = self._module_table.flags
        try:
            flags = cast(ToolFlags, flag_manager.get_section("tools"))
        except Exception:
            # Not yet registered — register now.  Other subsystems may
            # have already loaded ``ToolFlags`` if they peeked at this
            # section early; the registration is idempotent on schema.
            flag_manager.register("tools", ToolFlags)
            flags = cast(ToolFlags, flag_manager.get_section("tools"))
        self._flags = flags
        self._bind_web_fetch_config()
        await self._start_web_bridge()

        prompts = self._module_table.prompts
        self._registry._prompt_manager = prompts

        for tool_cls in BUILTIN_TOOLS:
            if not flags.is_enabled(tool_cls.name):
                logger.info("tool %s disabled via ToolFlags — skipping", tool_cls.name)
                continue
            layer: Layer = "deferred" if tool_cls.should_defer else "core"
            tool = tool_cls()
            tool._prompt_manager = prompts
            self._registry.register(tool, layer=layer, module_table=self._module_table)

        # Register ToolSearchTool — needs a registry reference, so it
        # cannot go through the normal BUILTIN_TOOLS path.
        from kernel.agents.mustang.tools.builtin.tool_search import ToolSearchTool

        search_tool = ToolSearchTool(self._registry)
        search_tool._prompt_manager = prompts
        self._registry.register(search_tool, layer="core", module_table=self._module_table)

        # Register scriptable ReplTool only when the repl flag is on.
        if flags.repl:
            from kernel.agents.mustang.tools.builtin.repl_python import ReplTool

            repl_tool = ReplTool()
            repl_tool._prompt_manager = prompts
            self._registry.register(repl_tool, layer="core", module_table=self._module_table)

        # Wire MCPManager signal so MCP tools are auto-registered
        # when connections come up or change.  MCPManager starts before
        # ToolManager (see app.py), so its initial on_tools_changed has
        # already fired — we must do an immediate sync to pick up any
        # tools from servers that connected during MCPManager.startup().
        self._mcp_disconnect: Any = None
        try:
            from kernel.agents.mustang.mcp import MCPManager

            if self._module_table.has(MCPManager):
                mcp = self._module_table.get(MCPManager)
                self._mcp_disconnect = mcp.on_tools_changed.connect(self._sync_mcp)
                # Initial sync — MCPManager already connected its servers.
                await self._sync_mcp()
        except (ImportError, KeyError):
            pass  # MCP subsystem not loaded — no proxy tools.

        # Inject user-configured safe commands into BashTool/PowerShellTool/CmdTool.
        # ToolAuthorizer (step 3) already owns the permissions section via
        # bind_section; we use get_section (read-only view) to avoid the
        # single-writer conflict.
        self._bind_bash_safe_commands()

        logger.info(
            "ToolManager started with %d built-in tools",
            sum(1 for _ in self._registry.all_tools()),
        )

    def _bind_web_fetch_config(self) -> None:
        """Claim the user-managed WebFetch config section."""
        try:
            self._web_fetch_config = self._module_table.config.bind_section(
                file="config",
                section="web_fetch",
                schema=WebFetchConfig,
            )
        except ValueError:
            # A test may materialize the same section before ToolManager
            # startup.  Fall back to read-only access for safety.
            self._web_fetch_config = self._module_table.config.get_section(
                file="config",
                section="web_fetch",
                schema=WebFetchConfig,
            )
        self._hydrate_web_fetch_env_from_secrets()

    def _bind_bash_safe_commands(self) -> None:
        """Read ``permissions.bash_safe_commands`` and inject into BashTool.

        Uses ``config.get_section`` (read-only view) because ToolAuthorizer
        already owns this section via ``bind_section``.  Subscribes to the
        ``changed`` signal for hot-reload.
        """
        from kernel.agents.mustang.tool_authz.config_section import PermissionsSection

        shell_tool = (
            self._registry.lookup("Bash")
            or self._registry.lookup("PowerShell")
            or self._registry.lookup("Cmd")
        )
        if shell_tool is None or not hasattr(shell_tool, "extra_safe_commands"):
            return

        try:
            section = self._module_table.config.get_section(
                file="config", section="permissions", schema=PermissionsSection
            )
        except Exception:
            logger.debug(
                "ToolManager: could not read permissions section — skipping bash_safe_commands"
            )
            return

        shell_tool.extra_safe_commands = frozenset(section.get().bash_safe_commands)

        async def _on_permissions_changed(
            _old: PermissionsSection, new: PermissionsSection
        ) -> None:
            shell_tool.extra_safe_commands = frozenset(new.bash_safe_commands)  # type: ignore[union-attr]

        section.changed.connect(_on_permissions_changed)

    async def shutdown(self) -> None:
        """Drop registered tools + clear FileStateCache.

        REPL may own worker processes; shut down tools that expose a shutdown hook.
        """
        if self._mcp_disconnect is not None:
            self._mcp_disconnect()
        if self._web_bridge is not None:
            await self._web_bridge.shutdown()
            from kernel.agents.mustang.tools.web.fetch_backends.browser import (
                set_web_bridge_manager,
            )

            set_web_bridge_manager(None)
        for tool, _layer in self._registry.all_tools():
            shutdown = getattr(tool, "shutdown", None)
            if shutdown is not None:
                await shutdown()
        self._file_state.clear()
        logger.info("ToolManager: shutdown complete")

    # ------------------------------------------------------------------
    # MCP integration
    # ------------------------------------------------------------------

    async def _sync_mcp(self) -> None:
        """Refresh MCP proxy tools when MCPManager signals a change.

        Called via ``MCPManager.on_tools_changed`` signal.  Clears all
        existing MCP tools and re-registers from connected servers.
        """
        try:
            from kernel.agents.mustang.mcp import MCPManager
            from kernel.agents.mustang.tools.mcp_adapter import MCPAdapter

            mcp = self._module_table.get(MCPManager)
        except (ImportError, KeyError):
            return

        # 1. Remove old MCP tools.
        mcp_names = [
            name
            for name, (tool, _layer) in list(self._registry._tools.items())
            if name.startswith("mcp__")
        ]
        for name in mcp_names:
            self._registry.unregister(name)

        # 2. Register fresh tools from each connected server.
        registered = 0
        for server in mcp.get_connected():
            tools = await mcp.list_tools(server.name)
            for tool_def in tools:
                adapter = MCPAdapter(server.name, tool_def, mcp)
                adapter._prompt_manager = self._module_table.prompts
                try:
                    layer: Layer = "core" if adapter.always_load else "deferred"
                    self._registry.register(adapter, layer=layer)
                    registered += 1
                except ValueError as exc:
                    logger.warning("_sync_mcp: %s", exc)

        # 3. Register auth pseudo-tools for NeedsAuth servers.
        auth_registered = 0
        secrets = self._module_table.secrets
        if secrets is not None:
            from kernel.agents.mustang.mcp.types import NeedsAuthServer
            from kernel.agents.mustang.tools.builtin.mcp_auth import McpAuthTool

            for conn in mcp.get_connections().values():
                if isinstance(conn, NeedsAuthServer) and conn.server_url:
                    auth_tool = McpAuthTool(conn.name, conn.server_url, mcp, secrets)
                    auth_tool._prompt_manager = self._module_table.prompts
                    try:
                        self._registry.register(auth_tool, layer="core")
                        auth_registered += 1
                    except ValueError as exc:
                        logger.warning("_sync_mcp auth tool: %s", exc)

        logger.info(
            "ToolManager: synced %d MCP tools + %d auth tools",
            registered,
            auth_registered,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_tool(self, tool: Tool, *, layer: Layer = "core") -> None:
        """Register an external tool (e.g. from MemoryManager).

        Resolves and caches the input schema automatically via
        ``module_table``.  This is the public API for subsystems that
        need to add tools after ToolManager startup.
        """
        tool._prompt_manager = self._module_table.prompts
        self._registry.register(tool, layer=layer, module_table=self._module_table)

    def lookup(self, name: str) -> Tool | None:
        """Resolve a name (primary or alias) to a Tool instance."""
        return self._registry.lookup(name)

    def file_state(self) -> FileStateCache:
        """Return the shared ``FileStateCache`` for use in ``ToolContext``."""
        return self._file_state

    def web_fetch_config_model(self) -> WebFetchConfig:
        section = self._web_fetch_config
        if section is None:
            section = self._module_table.config.get_section(
                file="config",
                section="web_fetch",
                schema=WebFetchConfig,
            )
        return cast(WebFetchConfig, section.get())

    def web_fetch_config(self) -> dict[str, Any]:
        self._hydrate_web_fetch_env_from_secrets()
        config = self.web_fetch_config_model()
        public_backends: dict[str, dict[str, Any]] = {}
        for backend, values in config.backends.items():
            definition = get_definition(backend)
            public_values = {
                key: value
                for key, value in values.items()
                if key not in {"api_key_ref"} and not key.endswith("_key_ref")
            }
            if definition is not None and definition.requires_api_key:
                public_values["api_key"] = (
                    "configured" if self._current_web_fetch_api_key(definition) else "missing"
                )
            public_backends[backend] = public_values
        return {
            "backend": config.backend,
            "backends": public_backends,
        }

    async def _start_web_bridge(self) -> None:
        """Start the process-local WebBridge manager."""
        from kernel.agents.mustang.tools.web.fetch_backends.browser import set_web_bridge_manager
        from kernel.agents.mustang.tools.web.web_bridge import WebBridgeManager

        async def _persist_pairing(extension_id: str, secret: str) -> None:
            secrets = self._module_table.secrets
            if secrets is None:
                raise ValueError("SecretManager is not available")
            secret_ref = secrets.set(
                "web_bridge.extension.secret",
                secret,
                kind="web_bridge",
                metadata={"scope": "web_bridge", "extension_id": extension_id},
            )
            current = self.web_fetch_config_model()
            browser_config = dict(current.backends.get("browser", {}))
            browser_config.update(
                {
                    "extension_id": extension_id,
                    "secret_ref": secret_ref.ref,
                    "protocol_version": "web-bridge.v1",
                }
            )
            backends = dict(current.backends)
            backends["browser"] = browser_config
            await self._update_web_fetch_config(current.model_copy(update={"backends": backends}))

        def _read_secret() -> str | None:
            secrets = self._module_table.secrets
            if secrets is None:
                return None
            browser_config = self.web_fetch_config_model().backends.get("browser", {})
            secret_ref = browser_config.get("secret_ref")
            if not isinstance(secret_ref, str) or not secret_ref:
                return None
            return secrets.get(secret_ref)

        async def _reset_pairing() -> None:
            current = self.web_fetch_config_model()
            browser_config = dict(current.backends.get("browser", {}))
            secret_ref = browser_config.get("secret_ref")
            if isinstance(secret_ref, str) and secret_ref:
                secrets = self._module_table.secrets
                if secrets is not None:
                    secrets.delete(secret_ref)
            for key in ("extension_id", "secret_ref", "protocol_version"):
                browser_config.pop(key, None)
            backends = dict(current.backends)
            backends["browser"] = browser_config
            await self._update_web_fetch_config(current.model_copy(update={"backends": backends}))

        access_port = int(
            os.getenv("MUSTANG_ACCESS_PORT", os.getenv("MUSTANG_ACCESS_ROUTER_PORT", "8200"))
        )
        self._web_bridge = WebBridgeManager(
            access_port=access_port,
            persist_pairing=_persist_pairing,
            reset_pairing=_reset_pairing,
            read_secret=_read_secret,
        )
        await self._web_bridge.startup()
        set_web_bridge_manager(self._web_bridge)

    def web_fetch_backend_options(self) -> dict[str, Any]:
        self._hydrate_web_fetch_env_from_secrets()
        return build_backend_options(self.web_fetch_config_model())

    def web_bridge_status(self, *, include_pairing_token: bool = False) -> dict[str, Any]:
        if self._web_bridge is None:
            return {
                "status": "unavailable",
                "paired": False,
                "connected": False,
                "installUrl": "",
                "bridgeWsUrl": "",
                "protocolVersion": "web-bridge.v1",
                "message": "WebBridge is not running.",
            }
        return self._web_bridge.status(include_pairing_token=include_pairing_token)

    def web_bridge_pair_start(self) -> dict[str, Any]:
        if self._web_bridge is None:
            raise ValueError("WebBridge is not running")
        return self._web_bridge.pair_start()

    async def web_bridge_pair_reset(self) -> dict[str, Any]:
        if self._web_bridge is None:
            raise ValueError("WebBridge is not running")
        return await self._web_bridge.pair_reset()

    async def set_web_fetch_backend(
        self,
        backend: str,
        *,
        run_setup: bool = False,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        definition = get_definition(backend)
        if definition is None:
            valid = ", ".join(sorted(backend_ids()))
            raise ValueError(f"Unknown WebFetch backend {backend!r}. Valid values: {valid}")

        self._hydrate_web_fetch_env_from_secrets()
        current = self.web_fetch_config_model()
        if current.backend == definition.id and not api_key and not run_setup:
            return {
                "backend": definition.id,
                "changed": False,
                "setupRequired": False,
                "credentialRequired": False,
                "setupResult": None,
                "message": f"WebFetch backend {definition.id} is already selected.",
            }
        setup_result: dict[str, Any] | None = None
        if definition.id != "auto":
            if definition.requires_api_key:
                key_to_validate = (api_key or "").strip() or self._current_web_fetch_api_key(
                    definition
                )
                if not key_to_validate:
                    req = credential_request(definition)
                    return {
                        "backend": backend,
                        "changed": False,
                        "credentialRequired": True,
                        "credentialRequest": req,
                        "message": f"{definition.label} needs an API key before it can be selected.",
                    }
                validation = await self._validate_web_fetch_api_key(definition, key_to_validate)
                if not validation["ok"]:
                    return {
                        "backend": backend,
                        "changed": False,
                        "credentialRequired": True,
                        "credentialRequest": credential_request(definition),
                        "message": validation["message"],
                    }
                if api_key:
                    await self._store_web_fetch_api_key(definition, key_to_validate)
                    self._hydrate_web_fetch_env_from_secrets()

            if run_setup and build_setup_plan(definition) is not None:
                setup_result = await self._run_web_fetch_setup(definition)
                if not setup_result.get("ok"):
                    return {
                        "backend": backend,
                        "changed": False,
                        "setupRequired": True,
                        "setupPlan": build_setup_plan(definition),
                        "setupResult": setup_result,
                        "message": self._format_web_fetch_setup_failure(definition, setup_result),
                    }

            if not backend_is_available(definition):
                if definition.requires_api_key:
                    req = credential_request(definition)
                    return {
                        "backend": backend,
                        "changed": False,
                        "credentialRequired": True,
                        "credentialRequest": req,
                        "message": f"{definition.label} needs an API key before it can be selected.",
                    }
                if build_setup_plan(definition) is not None and not backend_is_installed(
                    definition
                ):
                    if not run_setup:
                        return {
                            "backend": backend,
                            "changed": False,
                            "setupRequired": True,
                            "setupPlan": build_setup_plan(definition),
                            "message": f"{definition.label} dependencies are not installed.",
                        }
                if not backend_is_available(definition):
                    return {
                        "backend": backend,
                        "changed": False,
                        "message": self._format_web_fetch_unavailable(definition),
                    }

        latest = self.web_fetch_config_model()
        changed = latest.backend != definition.id
        await self._update_web_fetch_config(latest.model_copy(update={"backend": definition.id}))
        return {
            "backend": definition.id,
            "changed": changed,
            "setupRequired": False,
            "credentialRequired": False,
            "setupResult": setup_result,
            "message": f"WebFetch backend set to {definition.id}.",
        }

    async def _run_web_fetch_setup(self, definition: Any) -> dict[str, Any]:
        return await run_setup(definition)

    def _format_web_fetch_setup_failure(self, definition: Any, setup_result: dict[str, Any]) -> str:
        logs = setup_result.get("logs")
        if not isinstance(logs, list) or not logs:
            return f"{definition.label} setup failed with no setup logs."
        failed = next(
            (
                log
                for log in reversed(logs)
                if isinstance(log, dict) and log.get("exitCode") not in (0, None)
            ),
            logs[-1],
        )
        if not isinstance(failed, dict):
            return f"{definition.label} setup failed: {failed}"
        command = str(failed.get("command") or "unknown command")
        exit_code = failed.get("exitCode")
        stderr = str(failed.get("stderr") or "").strip()
        stdout = str(failed.get("stdout") or "").strip()
        details = stderr or stdout or "no command output"
        if len(details) > 1200:
            details = f"...{details[-1200:]}"
        return f"{definition.label} setup failed while running `{command}` (exit {exit_code}): {details}"

    def _format_web_fetch_unavailable(self, definition: Any) -> str:
        if getattr(definition, "id", "") == "browser":
            status = self.web_bridge_status(include_pairing_token=False)
            install_url = status.get("installUrl") or "/webfetch browser install"
            if not status.get("paired"):
                return (
                    "Browser backend needs WebBridge pairing first. "
                    f"Run /webfetch browser install or open {install_url}."
                )
            if not status.get("connected"):
                return (
                    "Browser backend is paired but the WebBridge extension is offline. "
                    "Open Chrome, make sure the DeepCLI WebBridge extension is enabled, "
                    "then run /webfetch browser status."
                )
            return "Browser backend is not available even though WebBridge reports connected."
        return f"{definition.label} is not available. Check dependencies or API credentials."

    async def set_web_fetch_config_value(self, path: str, value: Any) -> dict[str, Any]:
        parts = [part for part in path.split(".") if part]
        if len(parts) != 2:
            raise ValueError("WebFetch config path must be <backend>.<key>")
        backend, key = parts
        if backend not in backend_ids() or backend == "auto":
            raise ValueError(f"Unknown configurable WebFetch backend: {backend!r}")
        definition = get_definition(backend)
        normalized_key = key.strip().lower()
        if normalized_key in {"api_key", "apikey"}:
            if definition is None or not definition.requires_api_key:
                raise ValueError(f"{backend!r} does not use an API key")
            api_key = str(value or "").strip()
            if not api_key:
                raise ValueError("API key must not be empty")
            validation = await self._validate_web_fetch_api_key(definition, api_key)
            if not validation["ok"]:
                raise ValueError(validation["message"])
            await self._store_web_fetch_api_key(definition, api_key)
            self._hydrate_web_fetch_env_from_secrets()
            return self.web_fetch_config()
        if normalized_key == "api_key_ref" or normalized_key.endswith("_key_ref"):
            raise ValueError("Secret references are internal; use <backend>.api_key instead")
        current = self.web_fetch_config_model()
        backend_config = dict(current.backends.get(backend, {}))
        backend_config[key] = value
        backends = dict(current.backends)
        backends[backend] = backend_config
        updated = current.model_copy(update={"backends": backends})
        await self._update_web_fetch_config(updated)
        return self.web_fetch_config()

    async def _update_web_fetch_config(self, config: WebFetchConfig) -> None:
        section = self._web_fetch_config
        if section is None or not hasattr(section, "update"):
            section = self._module_table.config.bind_section(
                file="config",
                section="web_fetch",
                schema=WebFetchConfig,
            )
            self._web_fetch_config = section
        await section.update(config)

    def _hydrate_web_fetch_env_from_secrets(self) -> None:
        config = self.web_fetch_config_model()
        secrets = self._module_table.secrets
        if secrets is None:
            return
        for backend, values in config.backends.items():
            definition = get_definition(backend)
            if definition is None:
                continue
            env_key = primary_api_key_env(definition)
            if env_key is None or os.getenv(env_key, "").strip():
                continue
            secret_ref = values.get("api_key_ref")
            if not isinstance(secret_ref, str) or not secret_ref:
                continue
            secret_value = secrets.get(secret_ref)
            if secret_value:
                os.environ[env_key] = secret_value

    def _current_web_fetch_api_key(self, definition: Any) -> str:
        env_key = primary_api_key_env(definition)
        if env_key is None:
            return ""
        return os.getenv(env_key, "").strip()

    async def _store_web_fetch_api_key(self, definition: Any, api_key: str) -> None:
        secrets = self._module_table.secrets
        if secrets is None:
            raise ValueError("SecretManager is not available")
        req = credential_request(definition)
        if req is None:
            raise ValueError(f"{definition.label} does not accept API key credentials")
        secret_name = str(req["secretName"])
        secret_ref = secrets.set(
            secret_name,
            api_key,
            kind="api_key",
            metadata={"scope": "web_fetch", "backend": definition.id},
        )
        env_key = primary_api_key_env(definition)
        if env_key is not None:
            os.environ[env_key] = api_key
        current = self.web_fetch_config_model()
        backend_config = dict(current.backends.get(definition.id, {}))
        backend_config["api_key_ref"] = secret_ref.ref
        backends = dict(current.backends)
        backends[definition.id] = backend_config
        await self._update_web_fetch_config(current.model_copy(update={"backends": backends}))

    async def _validate_web_fetch_api_key(self, definition: Any, api_key: str) -> dict[str, Any]:
        env_key = primary_api_key_env(definition)
        if env_key is None:
            return {"ok": True, "message": ""}
        old_value = os.environ.get(env_key)
        os.environ[env_key] = api_key
        try:
            from kernel.agents.mustang.tools.web.fetch_backends import get_backend_by_name

            backend = get_backend_by_name(definition.id)
            if backend is None:
                return {
                    "ok": False,
                    "message": f"{definition.label} backend is not available after setting API key.",
                }
            result = await backend.fetch("https://example.com", max_chars=4_000)
            if result.error:
                return {
                    "ok": False,
                    "message": f"{definition.label} API key validation failed: {result.error}",
                }
            if not result.content.strip():
                return {
                    "ok": False,
                    "message": f"{definition.label} API key validation returned no content.",
                }
            return {"ok": True, "message": ""}
        except Exception as exc:
            return {
                "ok": False,
                "message": f"{definition.label} API key validation failed: {self._format_web_fetch_validation_exception(exc)}",
            }
        finally:
            if old_value is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = old_value

    def _format_web_fetch_validation_exception(self, exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            response = exc.response
            detail = ""
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    detail = str(
                        payload.get("error")
                        or payload.get("message")
                        or payload.get("detail")
                        or payload
                    )
            except Exception:
                detail = response.text.strip()
            suffix = f": {detail}" if detail else ""
            return f"HTTP {response.status_code} from {response.request.url}{suffix}"
        return str(exc)

    def snapshot_for_session(
        self,
        *,
        session_id: str,
        plan_mode: bool = False,
        agent_whitelist: set[str] | None = None,
    ) -> ToolSnapshot:
        """Build a per-turn snapshot of visible tools.

        Consults ``ToolAuthorizer.filter_denied_tools`` (when available)
        to strip deny-listed tools from the pool entirely — the LLM
        never sees them.  Session-level defense-in-depth with
        ``ToolAuthorizer.authorize()``, which is also called at each
        tool-call site.

        ``session_id`` is reserved for future per-session policy
        (e.g. rate limits); currently unused.
        """
        denied: set[str] = set()
        # ToolAuthorizer is step 3, ToolManager is step 5 — authorizer
        # is always up by the time we snapshot, except in degraded mode
        # where it failed to load.  Handle the missing case gracefully.
        try:
            from kernel.agents.mustang.tool_authz import ToolAuthorizer

            authorizer = self._module_table.get(ToolAuthorizer)
        except (KeyError, ImportError):
            authorizer = None

        if authorizer is not None:
            all_names = {tool.name for tool, _ in self._registry.all_tools()}
            denied = authorizer.filter_denied_tools(all_names)

        repl_mode = self._flags is not None and self._flags.repl
        return self._registry.snapshot(
            plan_mode=plan_mode,
            repl_mode=repl_mode,
            agent_whitelist=agent_whitelist,
            denied_names=denied,
        )


__all__ = [
    "DiffDisplay",
    "FileDisplay",
    "FileState",
    "FileStateCache",
    "Layer",
    "LocationsDisplay",
    "PermissionSuggestion",
    "RawBlocks",
    "TextDisplay",
    "Tool",
    "ToolCallProgress",
    "ToolCallResult",
    "ToolContext",
    "ToolDisplayPayload",
    "ToolFlags",
    "ToolInputError",
    "ToolManager",
    "ToolRegistry",
    "ToolSnapshot",
    "hash_text",
    "matches_name",
]
