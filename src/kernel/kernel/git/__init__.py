"""GitManager — centralised git operations subsystem.

Design reference: ``docs/plans/pending/worktree-and-git-context.md``.

Key design decisions:

- **startup() never fails** — sets ``_available = False`` instead of
  raising.  GitManager must stay in the module table so it can receive
  ConfigManager signals when the user installs git or sets
  ``git.binary`` mid-session.
- **Dynamic tool registration** — EnterWorktree / ExitWorktree are
  registered/unregistered in the deferred layer as ``_available``
  toggles.  LLM only sees tools when git is actually usable.
- **WorktreeStore** — SQLite persistence in ``kernel.db`` for
  crash-recovery GC and session-resume cwd restore.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kernel.git.context import build_git_context
from kernel.git.store import WorktreeStore
from kernel.git.types import GitConfig, GitContext, GitTimeoutError, WorktreeSession
from kernel.git.worktree import count_changes, remove_worktree
from kernel.subsystem import Subsystem

if TYPE_CHECKING:
    from kernel.module_table import KernelModuleTable

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 5.0  # seconds


class GitManager(Subsystem):
    """Centralised git operations subsystem.

    Owns: binary resolution, command execution, git context snapshots,
    worktree lifecycle, dynamic tool registration, and shutdown cleanup.
    """

    def __init__(self, module_table: KernelModuleTable) -> None:
        super().__init__(module_table)
        self._git_bin: str | None = None
        self._available: bool = False
        self._tools_registered: bool = False
        self._store: WorktreeStore | None = None
        self._disconnect_config: Any = None

        # session_id → WorktreeSession (memory cache, persisted in SQLite)
        self._worktrees: dict[str, WorktreeSession] = {}
        # session_id → GitContext (session-level cache)
        self._context_cache: dict[str, GitContext | None] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Initialise the git subsystem.  **Never raises.**"""
        try:
            # 1. Open WorktreeStore (shared kernel.db).
            db_path = self._module_table.state_dir / "kernel.db"
            self._store = WorktreeStore(db_path)
            await self._store.open()

            # 2. Resolve git binary (user config > PATH > unavailable).
            self._resolve_binary()

            # 3. Subscribe to config changes for hot-reload.
            self._subscribe_config()

            # 4. Startup GC — clean up crash-orphaned worktrees.
            await self._gc_stale_worktrees()

            # 5. Register tools if git is available.
            self._sync_tools()

            logger.info(
                "GitManager started: available=%s, binary=%s",
                self._available,
                self._git_bin,
            )
        except Exception:
            logger.exception("GitManager startup error — running in degraded mode")
            self._available = False

    async def shutdown(self) -> None:
        """Clean up worktrees and release resources."""
        # Disconnect config signal.
        if self._disconnect_config is not None:
            self._disconnect_config()
            self._disconnect_config = None

        # Clean up active worktrees (no-change ones auto-removed).
        for sid, ws in list(self._worktrees.items()):
            if not self._available:
                break
            try:
                changes = await count_changes(self, ws.worktree_path)
                if changes == 0:
                    await remove_worktree(self, ws)
                    if self._store is not None:
                        await self._store.delete(sid)
                    logger.info("Cleaned up worktree %s (session %s)", ws.slug, sid)
                else:
                    # Keep worktree + DB record for next startup GC.
                    logger.warning(
                        "Worktree %s has %d uncommitted change(s), keeping",
                        ws.slug,
                        changes,
                    )
            except Exception:
                logger.exception("Failed to cleanup worktree %s", ws.slug)

        self._worktrees.clear()
        self._context_cache.clear()

        # Close store.
        if self._store is not None:
            await self._store.close()
            self._store = None

    # ------------------------------------------------------------------
    # Binary resolution + config signal
    # ------------------------------------------------------------------

    def _resolve_binary(self) -> None:
        """Find git binary: user config → system PATH → unavailable."""
        # 1. User config priority.
        try:
            config = self._module_table.config
            section = config.get_section(file="git", section="git", schema=GitConfig)
            if section is not None:
                cfg = section.get()
                if isinstance(cfg, GitConfig) and cfg.binary:
                    resolved = shutil.which(cfg.binary)
                    if resolved:
                        self._git_bin = resolved
                        self._available = True
                        return
                    logger.warning("Configured git binary %r not found", cfg.binary)
        except Exception:
            pass  # Config section may not exist yet.

        # 2. System PATH fallback.
        system_bin = shutil.which("git")
        if system_bin:
            self._git_bin = system_bin
            self._available = True
            return

        # 3. Unavailable.
        self._git_bin = None
        self._available = False
        logger.info("Git binary not found — git features disabled")

    def _subscribe_config(self) -> None:
        """Listen for ``git.binary`` changes via ConfigManager signal."""
        try:
            config = self._module_table.config
            section = config.bind_section(file="git", section="git", schema=GitConfig)
            self._disconnect_config = section.changed.connect(self._on_config_changed)
        except Exception:
            logger.debug("Could not bind git config section", exc_info=True)

    async def _on_config_changed(self, old: GitConfig, new: GitConfig) -> None:
        """Handle ``git.binary`` changes at runtime."""
        old_available = self._available
        self._resolve_binary()
        if self._available != old_available:
            self._sync_tools()
        # Binary may have changed — invalidate all cached contexts.
        self._context_cache.clear()

    # ------------------------------------------------------------------
    # Dynamic tool registration
    # ------------------------------------------------------------------

    def _sync_tools(self) -> None:
        """Register worktree tools.

        The tools are always registered regardless of git availability —
        they dispatch to the git path when git is available and fall
        back to WORKTREE_CREATE / WORKTREE_REMOVE hooks otherwise (CC
        parity).  The decision happens at call time inside the tool,
        not at registration time, so the LLM always sees these tools in
        its deferred pool.
        """
        try:
            from kernel.tools import ToolManager

            tool_mgr = self._module_table.get(ToolManager)
        except (KeyError, ImportError):
            return  # ToolManager not loaded.

        if self._tools_registered:
            return

        from kernel.tools.builtin.enter_worktree import EnterWorktreeTool
        from kernel.tools.builtin.exit_worktree import ExitWorktreeTool

        # Skip if already present (e.g. ToolManager registered them via
        # BUILTIN_TOOLS — not currently the case, but defensive).
        if tool_mgr.lookup("EnterWorktree") is None:
            enter = EnterWorktreeTool()
            enter._prompt_manager = self._module_table.prompts
            tool_mgr._registry.register(enter, layer="deferred")
        if tool_mgr.lookup("ExitWorktree") is None:
            exit_ = ExitWorktreeTool()
            exit_._prompt_manager = self._module_table.prompts
            tool_mgr._registry.register(exit_, layer="deferred")
        self._tools_registered = True
        logger.info(
            "Registered EnterWorktree/ExitWorktree (git=%s; hook fallback available)",
            self._available,
        )

    # ------------------------------------------------------------------
    # Startup GC — clean crash-orphaned worktrees
    # ------------------------------------------------------------------

    async def _gc_stale_worktrees(self) -> None:
        """Clean up worktrees left behind by a previous crash."""
        if not self._available or self._store is None:
            return
        stale = await self._store.list_all()
        for ws in stale:
            try:
                if not ws.worktree_path.exists():
                    await self._store.delete(ws.session_id)
                    logger.info(
                        "GC: worktree dir gone for %s, cleaned DB record",
                        ws.slug,
                    )
                    continue
                changes = await count_changes(self, ws.worktree_path)
                if changes == 0:
                    await remove_worktree(self, ws)
                    await self._store.delete(ws.session_id)
                    logger.info("GC: cleaned up stale worktree %s", ws.slug)
                else:
                    logger.warning(
                        "GC: stale worktree %s has %d uncommitted change(s), keeping",
                        ws.slug,
                        changes,
                    )
            except Exception:
                logger.exception("GC: failed to process worktree %s", ws.slug)

    # ------------------------------------------------------------------
    # Public API: availability
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether git commands can be executed."""
        return self._available

    # ------------------------------------------------------------------
    # Public API: git command execution
    # ------------------------------------------------------------------

    async def run(
        self,
        args: list[str],
        cwd: Path,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> tuple[int, str, str]:
        """Execute a git command.  Returns ``(returncode, stdout, stderr)``.

        Uses ``self._git_bin`` (user config > system PATH).
        """
        if self._git_bin is None:
            raise RuntimeError("run() called when git is unavailable")

        proc = await asyncio.create_subprocess_exec(
            self._git_bin,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise GitTimeoutError(f"git {args[0]} timed out after {timeout}s")
        assert proc.returncode is not None
        return proc.returncode, stdout_bytes.decode(), stderr_bytes.decode()

    async def run_ok(
        self,
        args: list[str],
        cwd: Path,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> str | None:
        """Execute git; return ``stdout.strip()`` on success, ``None`` on failure."""
        try:
            rc, out, _ = await self.run(args, cwd, timeout=timeout)
            return out.strip() if rc == 0 else None
        except (GitTimeoutError, Exception):
            return None

    # ------------------------------------------------------------------
    # Public API: git context
    # ------------------------------------------------------------------

    async def get_context(self, cwd: Path, session_id: str) -> GitContext | None:
        """Return a session-level cached git context snapshot.

        Computed on first call; subsequent calls return the cache.
        Call :meth:`invalidate_context` after cwd changes (worktree
        enter/exit).
        """
        if not self._available:
            return None
        if session_id not in self._context_cache:
            self._context_cache[session_id] = await build_git_context(self, cwd)
        return self._context_cache.get(session_id)

    def invalidate_context(self, session_id: str) -> None:
        """Clear cached git context for a session (e.g. after worktree switch)."""
        self._context_cache.pop(session_id, None)

    # ------------------------------------------------------------------
    # Public API: worktree tracking
    # ------------------------------------------------------------------

    async def register_worktree(self, ws: WorktreeSession) -> None:
        """Register a worktree and persist to SQLite."""
        self._worktrees[ws.session_id] = ws
        if self._store is not None:
            await self._store.insert(ws)

    async def unregister_worktree(self, session_id: str) -> WorktreeSession | None:
        """Unregister a worktree and remove the SQLite record."""
        ws = self._worktrees.pop(session_id, None)
        if self._store is not None:
            await self._store.delete(session_id)
        return ws

    def get_worktree(self, session_id: str) -> WorktreeSession | None:
        """Look up the active worktree for a session (memory only)."""
        return self._worktrees.get(session_id)

    async def restore_worktree_for_session(self, session_id: str) -> WorktreeSession | None:
        """Restore a worktree for a reconnecting session.

        Checks memory first, then SQLite.  Validates that the worktree
        directory still exists; cleans up the DB record if it doesn't.
        """
        if not self._available or self._store is None:
            return None

        # Already in memory.
        if session_id in self._worktrees:
            return self._worktrees[session_id]

        # Query DB.
        ws = await self._store.get_by_session(session_id)
        if ws is None:
            return None

        # Validate directory.
        if not ws.worktree_path.exists() or not (ws.worktree_path / ".git").is_file():
            await self._store.delete(session_id)
            logger.warning("Worktree %s no longer valid, cleaned DB record", ws.slug)
            return None

        # Restore to memory.
        self._worktrees[session_id] = ws
        self.invalidate_context(session_id)
        return ws


__all__ = ["GitManager"]
