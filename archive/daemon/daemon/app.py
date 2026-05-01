"""FastAPI application factory and lifespan management.

The ``create_app()`` function is called by uvicorn (see ``__main__.py``).
It wires together config, providers, extensions, session manager, and
auth — then mounts the API routes.

Shutdown is handled by ``lifecycle.run_cleanups()`` — individual
subsystems register their own cleanup callbacks during startup, so
this module never needs to know about specific shutdown steps.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from daemon.api.health import router as health_router
from daemon.api.ws import router as ws_router
from daemon.auth import ensure_auth_token
from daemon.config.loader import load_config
from daemon.extensions.manager import ExtensionManager
from daemon.lifecycle import register_cleanup, run_cleanups
from daemon.providers.registry import ProviderRegistry
from daemon.sessions.cleanup import cleanup_expired_sessions, start_cleanup_task
from daemon.sessions.manager import SessionManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown lifecycle.

    Startup:
      1. Load config.
      2. Build provider registry.
      3. Load extensions (tools, skills, hooks, MCP servers).
      4. Create session manager (replaces the former global
         orchestrator — each session gets its own orchestrator).
      5. Generate auth token.

    Shutdown:
      Run all registered cleanup callbacks via lifecycle module.
    """
    config = load_config()
    registry = ProviderRegistry.from_config(config)

    # Load extensions (tools + skills + hooks + MCP)
    ext_manager = ExtensionManager(config)
    await ext_manager.load_all()

    # --- agent-browser lifecycle ----------------------------------------
    # (1) Synchronously reap any stale agent-browser Rust daemon + bundled
    #     Chrome children left behind by a previous Mustang run (crash,
    #     kill -9, uvicorn reload).  This MUST complete before we accept
    #     requests, otherwise the first page_fetch could hang for 30s
    #     against a zombie Unix socket.  The reap itself is bounded — it
    #     walks the runtime dir and /proc, no external I/O — so blocking
    #     startup on it is cheap.
    # (2) Fire off preheat as a background task so the next page_fetch /
    #     browser call pays only navigation cost, not Chrome cold-start.
    #     Shutdown cancels the task if still pending.
    from daemon.extensions.tools.builtin.agent_browser_cli import (
        preheat as _browser_preheat,
        reap_stale_daemon as _browser_reap,
    )

    try:
        await _browser_reap()
    except Exception:
        logger.exception("agent-browser stale-daemon reap failed on startup")

    preheat_task = asyncio.create_task(_browser_preheat(), name="agent-browser-preheat")

    async def _cancel_preheat() -> None:
        if preheat_task.done():
            return
        preheat_task.cancel()
        try:
            await preheat_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    register_cleanup(_cancel_preheat)

    session_manager = SessionManager(
        registry=registry,
        config=config,
        ext_manager=ext_manager,
    )

    auth_token = ensure_auth_token()

    # Session cleanup — run once at startup, then schedule background task.
    cleanup_expired_sessions(
        session_manager._session_dir,  # noqa: SLF001
        config.sessions,
        active_session_ids=set(),
    )
    cleanup_task = await start_cleanup_task(
        session_manager._session_dir,  # noqa: SLF001
        config.sessions,
        get_active_ids=lambda: {s.session_id for s in session_manager.active_sessions()},
    )

    async def _cancel_cleanup() -> None:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

    register_cleanup(_cancel_cleanup)

    # Store on app.state so handlers can access them
    app.state.config = config
    app.state.registry = registry
    app.state.ext_manager = ext_manager
    app.state.session_manager = session_manager
    app.state.auth_token = auth_token

    logger.info(
        "Mustang daemon started — %s:%d, %d tools, %d skills, %d hooks",
        config.daemon.host,
        config.daemon.port,
        len(ext_manager.tool_registry),
        len(ext_manager.skill_registry),
        ext_manager.hook_registry.hook_count,
    )

    yield

    # Shutdown: each subsystem registered its own cleanup callback
    await run_cleanups()
    logger.info("Mustang daemon stopped")


def create_app() -> FastAPI:
    """Build the FastAPI application with all routes and middleware."""
    app = FastAPI(
        title="Mustang Daemon",
        version="0.1.0",
        lifespan=_lifespan,
    )

    app.include_router(health_router)
    app.include_router(ws_router)

    return app
