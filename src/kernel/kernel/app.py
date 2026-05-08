from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from fastapi import FastAPI

import kernel
from kernel.access_agent import AccessAgentState
from kernel.agent_hub import AgentHub, AgentHubManager
from kernel.agents import default_primary_agent_definition
from kernel.connection_auth import ConnectionAuthenticator
from kernel.commands import CommandManager
from kernel.config import ConfigManager
from kernel.flags import FlagManager, KernelFlags
from kernel.gateways import GatewayManager
from kernel.secrets import SecretManager
from kernel.git import GitManager
from kernel.hooks import HookManager
from kernel.mcp import MCPManager
from kernel.memory import MemoryManager
from kernel.module_table import KernelModuleTable
from kernel.paths import user_home, user_path, user_state_dir
from kernel.prompts import PromptManager
from kernel.llm import LLMManager
from kernel.llm_provider import LLMProviderManager
from kernel.protocol.flags import ProtocolFlags
from kernel.routes.flags import TransportFlags
from kernel.schedule import ScheduleManager
from kernel.session import SessionManager
from kernel.skills import SkillManager
from kernel.subsystem import Subsystem
from kernel.tool_authz import ToolAuthorizer
from kernel.tools import ToolManager

logger = logging.getLogger(__name__)

# Regular subsystems are loaded AFTER the bootstrap services
# (FlagManager, ConfigManager) are up.  Every class here inherits
# from ``kernel.subsystem.Subsystem`` and goes through
# ``Subsystem.load`` / ``Subsystem.unload``.

# Core subsystems: always loaded, degraded on failure.
_CORE_SUBSYSTEMS: list[tuple[str, type[Subsystem]]] = [
    ("connection_auth", ConnectionAuthenticator),
    # Step 3: ToolAuthorizer — must be earlier than Tools (step 5) and
    # Session (step 10) so OrchestratorDeps can always pick it up.
    ("tool_authz", ToolAuthorizer),
    ("provider", LLMProviderManager),  # Provider instance lifecycle (no config)
    ("llm", LLMManager),  # Model config + routing (depends on provider)
]

# Optional subsystems: gated by KernelFlags.  Name must match the
# corresponding KernelFlags field name.
_OPTIONAL_SUBSYSTEMS: list[tuple[str, type[Subsystem]]] = [
    ("mcp", MCPManager),  # MCP before Tools — ToolManager connects to MCPManager's signal
    ("tools", ToolManager),
    ("skills", SkillManager),
    ("hooks", HookManager),
    ("memory", MemoryManager),
    ("git", GitManager),  # after tools — _sync_tools needs ToolManager
]

# Trailing core subsystems: must start after everything else.
# Order matters: session must be first (orchestrators depend on tools/skills/
# hooks/mcp/memory), then commands (session-state queries), then gateways
# (depends on both session and commands).
_TRAILING_SUBSYSTEMS: list[tuple[str, type[Subsystem]]] = [
    ("session", cast(type[Subsystem], SessionManager)),
    ("commands", CommandManager),
    ("gateways", GatewayManager),
    ("schedule", ScheduleManager),  # after session + gateways
]


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ------------------------------------------------------------------
    # Bootstrap services — NOT Subsystem subclasses.  They have richer
    # public APIs than the uniform startup/shutdown contract and every
    # regular subsystem depends on them already being up, so the
    # lifespan constructs them explicitly and treats their failures
    # as fatal to kernel boot.  They live on the module table as
    # dedicated typed attributes (``flags`` / ``config``) rather than
    # inside the subsystem dict.
    # ------------------------------------------------------------------

    # --- 0. FlagManager (fatal on failure) ---
    # Without flags we cannot decide which optional subsystems to
    # load, so a failure here aborts kernel boot.
    flags = FlagManager()
    try:
        await flags.initialize()
    except Exception:
        logger.critical("FlagManager failed to initialize — aborting kernel")
        raise
    kernel_flags = cast(KernelFlags, flags.get_section("kernel"))

    # Transport is not a Subsystem (its lifecycle is bound to the
    # FastAPI server itself), so it has no startup hook from which
    # to register its own flag section.  Do it here, right after
    # FlagManager.initialize, so a misspelled stack name in
    # flags.yaml raises pydantic.ValidationError and aborts boot
    # instead of surfacing as a runtime guard in transport code.
    try:
        flags.register("transport", TransportFlags)
    except Exception:
        logger.critical("TransportFlags failed to register — aborting kernel")
        raise

    try:
        flags.register("protocol", ProtocolFlags)
    except Exception:
        logger.critical("ProtocolFlags failed to register — aborting kernel")
        raise

    # --- 1. SecretManager (fatal on failure) ---
    # Must be ready before ConfigManager so ${secret:name} references
    # in YAML config files can be expanded.
    secrets = SecretManager()
    try:
        await secrets.startup()
    except Exception:
        logger.critical("SecretManager failed to start — aborting kernel")
        raise

    # --- 2. ConfigManager (fatal on failure) ---
    # Every regular subsystem reads its config from here.
    config = ConfigManager(secret_resolver=secrets.get)
    try:
        await config.startup()
    except Exception:
        logger.critical("ConfigManager failed to start — aborting kernel")
        raise

    # --- 2. PromptManager (fatal on failure) ---
    # All prompt text lives in .txt files (D18).  PromptManager loads
    # built-in defaults then overlays user override layers on top.
    # Lookup order (highest priority first):
    #   <project>/.mustang/prompts/  →  <DeepCLI home>/prompts/  →  default/
    _pm_user_dirs: list[Path] = []
    for _d in [user_path("prompts"), Path.cwd() / ".mustang" / "prompts"]:
        if _d.is_dir():
            _pm_user_dirs.append(_d)
    prompts = PromptManager(user_dirs=_pm_user_dirs or None)
    try:
        prompts.load()
    except Exception:
        logger.critical("PromptManager failed to load — aborting kernel")
        raise

    # ------------------------------------------------------------------
    # Kernel-wide state directory — home for subsystem runtime
    # artifacts (auth tokens, memory indices, session metadata, ...).
    # Separate from ``<DeepCLI home>/config/`` (user-edited intent, including
    # ``config/flags.yaml`` feature switches) so the two
    # categories never bleed into each other.  The directory is
    # created with 0o700 because it holds secrets (notably the auth
    # token file); the mode is only applied on creation — existing
    # directories are trusted as-is so we don't clobber an admin's
    # deliberate choice.
    # ------------------------------------------------------------------
    state_dir = user_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    # ------------------------------------------------------------------
    # Module table — single registry of every live kernel module.
    # Bootstrap services occupy typed attributes; regular subsystems go
    # into an internal class-keyed dict populated by ``register()``
    # after a successful ``Subsystem.load``.  Routes and handlers reach
    # everything via ``app.state.module_table``.
    # ------------------------------------------------------------------
    module_table = KernelModuleTable(
        flags=flags,
        config=config,
        state_dir=state_dir,
        secrets=secrets,
        prompts=prompts,
    )
    app.state.module_table = module_table
    primary_definition = default_primary_agent_definition(
        home=user_home(),
        workspace=str(Path.cwd()),
    )
    agent_hub = AgentHub(
        manager=AgentHubManager([primary_definition]),
    )
    agent_hub.router.update_snapshot(agent_hub.manager.routing_snapshot(revision=1))
    access_agent_state = AccessAgentState()
    hub_endpoint = os.getenv("MUSTANG_AGENT_HUB_ENDPOINT")
    if hub_endpoint:
        access_agent_state.hub_endpoint = hub_endpoint
        module_table.agent_hub_endpoint = hub_endpoint
    else:
        access_agent_state.mark_hub_ready()
    app.state.agent_hub = agent_hub
    module_table.agent_hub = agent_hub
    app.state.access_agent_state = access_agent_state

    async def _load(name: str, factory: type[Subsystem]) -> None:
        instance = await factory.load(name, module_table)
        if instance is None:
            return
        module_table.register(instance)

    # --- 3. Core subsystems (always on, degraded on failure) ---
    for name, factory in _CORE_SUBSYSTEMS:
        await _load(name, factory)

    # --- 4. Optional subsystems (skipped entirely when disabled) ---
    for name, factory in _OPTIONAL_SUBSYSTEMS:
        if not getattr(kernel_flags, name):
            logger.info("Subsystem %s disabled via kernel flags — skipping", name)
            continue
        await _load(name, factory)

    # --- 5. Trailing core subsystems (session → commands → gateways) ---
    for trailing_name, trailing_factory in _TRAILING_SUBSYSTEMS:
        # Commands and gateways are gated by KernelFlags so they can be
        # disabled without touching the optional-subsystem group.
        if not getattr(kernel_flags, trailing_name, True):
            logger.info("Subsystem %s disabled via kernel flags — skipping", trailing_name)
            continue
        await _load(trailing_name, trailing_factory)
        if (
            trailing_name == "session"
            and module_table.has(cast(type[Subsystem], SessionManager))
            and not hub_endpoint
        ):
            access_agent_state.mark_primary_registered()

    yield

    # --- Unload regular subsystems (reverse load order) ---
    # dict insertion order gives us the load sequence for free.
    for subsystem in reversed(module_table.subsystems()):
        await subsystem.unload()

    # --- Bootstrap services need no teardown ---
    # FlagManager is runtime-frozen and ConfigManager section updates
    # persist synchronously, so nothing is left in memory to drain.


def create_app() -> FastAPI:
    """Build the FastAPI application with all routes and middleware."""
    if os.getenv("_MUSTANG_DEV"):
        import sys

        kernel_logger = logging.getLogger("kernel")
        kernel_logger.setLevel(logging.INFO)
        if not kernel_logger.handlers:
            kernel_logger.addHandler(logging.StreamHandler(sys.stderr))

    from kernel.routes import router

    app = FastAPI(
        title="Mustang Kernel",
        version=kernel.__version__,
        lifespan=_lifespan,
    )
    app.include_router(router)

    return app
