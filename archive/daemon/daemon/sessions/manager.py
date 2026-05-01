"""Session factory + registry (:class:`SessionManager`).

One :class:`SessionManager` lives on ``app.state`` for the lifetime
of the daemon process.  It holds shared infrastructure (providers,
config, extensions, permission settings, memory store) and creates
per-session :class:`Orchestrator` instances on demand.

Architecture::

    SessionManager (one per daemon, on app.state)
      └── Session A
      │     ├── Orchestrator (owns Conversation)
      │     ├── TranscriptWriter (append-only JSONL)
      │     └── connections: {ws1, ws2}   ← broadcast targets
      └── Session B
            └── ...

The :class:`Session` class itself lives in :mod:`daemon.sessions.session`;
the JSONL chain → :class:`Conversation` replay logic lives in
:mod:`daemon.sessions.rebuild`.  Both are re-exported here for
backward compatibility with existing callers.
"""

from __future__ import annotations

import logging
import uuid as _uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from daemon.config.schema import RuntimeConfig
from daemon.engine.conversation import Conversation
from daemon.extensions.manager import ExtensionManager
from daemon.permissions.engine import PermissionEngine
from daemon.permissions.modes import PermissionMode
from daemon.permissions.settings import PermissionSettings
from daemon.providers.registry import ProviderRegistry
from daemon.sessions.image_cache import ImageCache
from daemon.sessions.rebuild import rebuild_conversation
from daemon.sessions.session import Session
from daemon.sessions.storage import SessionMeta, TranscriptWriter

if TYPE_CHECKING:
    from daemon.engine.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

# Default session directory under the user's home.
_DEFAULT_SESSION_DIR = Path("~/.mustang/sessions").expanduser()
_DEFAULT_IMAGE_CACHE_DIR = Path("~/.mustang/cache/images").expanduser()

# Back-compat alias — tests import the old underscore name.
_rebuild_conversation = rebuild_conversation


class SessionManager:
    """Creates, resumes, and manages :class:`Session` instances.

    One ``SessionManager`` lives on ``app.state`` for the lifetime of
    the daemon process.  It holds shared infrastructure (providers,
    config, extensions) and creates per-session orchestrators on
    demand.

    Args:
        registry: Shared provider registry.
        config: Resolved runtime configuration.
        ext_manager: Shared extension manager (tools, skills, hooks).
        session_dir: Directory for JSONL / meta files.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        config: RuntimeConfig,
        ext_manager: ExtensionManager,
        session_dir: Path | None = None,
        permission_settings: PermissionSettings | None = None,
        memory_store: Any = None,
        image_cache: ImageCache | None = None,
    ) -> None:
        self._registry = registry
        self._config = config
        self._ext_manager = ext_manager
        self._session_dir = session_dir or _DEFAULT_SESSION_DIR
        self._sessions: dict[str, Session] = {}
        # Shared permission settings — one copy, referenced by every
        # session's PermissionEngine.  Rules persisted by one session
        # are immediately visible to others.
        if permission_settings is None:
            permission_settings = PermissionSettings()
            permission_settings.load()
        self._permission_settings = permission_settings
        # Shared cross-project memory store (Step 4.10).  Lazy-loaded
        # on first use if not provided.  One instance per daemon.
        if memory_store is None:
            from daemon.memory.store import MemoryStore

            memory_store = MemoryStore()
            memory_store.load()
        self._memory_store = memory_store
        # Shared image cache (Step 5.6).  Content-addressed store for
        # tool-returned image bytes; keeps JSONL slim.
        self._image_cache = image_cache or ImageCache(_DEFAULT_IMAGE_CACHE_DIR)

    # -- Create / get / resume ---------------------------------------

    def create(self, cwd: Path | None = None) -> Session:
        """Create a new session with a fresh orchestrator.

        Args:
            cwd: Working directory for this session (defaults to
                ``Path.cwd()``).

        Returns:
            A newly created :class:`Session`.
        """
        session_id = str(_uuid.uuid4())
        resolved_cwd = (cwd or Path.cwd()).resolve()

        orchestrator = self._build_orchestrator(cwd=resolved_cwd, session_id=session_id)

        provider_cfg = self._config.providers.get(self._config.default_provider)
        meta = SessionMeta(
            session_id=session_id,
            cwd=str(resolved_cwd),
            model=provider_cfg.model if provider_cfg else "",
            provider=self._config.default_provider,
        )
        writer = TranscriptWriter(self._session_dir, session_id, meta=meta)

        session = Session(session_id=session_id, orchestrator=orchestrator, writer=writer)
        self._sessions[session_id] = session

        logger.info("Created session %s (cwd=%s)", session_id[:8], resolved_cwd)
        return session

    def get(self, session_id: str) -> Session | None:
        """Look up an active (in-memory) session.

        Returns:
            The session if it is currently loaded, else ``None``.
        """
        return self._sessions.get(session_id)

    def resume(self, session_id: str) -> Session:
        """Restore a session from its persisted JSONL transcript.

        If the session is already active in memory, returns it
        directly without re-reading from disk.

        Steps:
          1. Read the JSONL chain via :meth:`TranscriptWriter.read_chain`.
          2. Rebuild a :class:`Conversation` from the entries.
          3. Create an :class:`Orchestrator` with that conversation.
          4. Register the session as active.

        Args:
            session_id: The session to resume.

        Returns:
            The resumed :class:`Session`.

        Raises:
            FileNotFoundError: If no transcript exists for this ID.
            ValueError: If the transcript is too large.
        """
        # Already in memory — reuse.
        existing = self._sessions.get(session_id)
        if existing is not None:
            return existing

        writer = TranscriptWriter(self._session_dir, session_id)

        if not writer.jsonl_path.exists():
            raise FileNotFoundError(f"No transcript found for session {session_id}")

        chain = writer.read_chain()
        conversation = rebuild_conversation(
            chain,
            cancelled_tool_policy=self._config.sessions.cancelled_tool_policy,
            image_cache=self._image_cache,
        )

        # Restore the writer's last_uuid so new entries chain correctly.
        if chain:
            writer.last_uuid = chain[-1].uuid

        orchestrator = self._build_orchestrator(
            cwd=Path(writer.meta.cwd) if writer.meta.cwd else None,
            conversation=conversation,
            session_id=session_id,
        )
        # Resumed sessions must re-fetch git status — the cached snapshot
        # (if it existed in the previous process) is stale across daemon
        # restarts / time gaps.  Fresh sessions don't need this since the
        # cache is unset by default.
        orchestrator.invalidate_git_status()

        session = Session(session_id=session_id, orchestrator=orchestrator, writer=writer)
        self._sessions[session_id] = session

        logger.info(
            "Resumed session %s (%d entries, %d messages)",
            session_id[:8],
            len(chain),
            conversation.message_count,
        )
        return session

    # -- List / delete -----------------------------------------------

    def list_sessions(self) -> list[SessionMeta]:
        """List all persisted sessions (scans meta.json files).

        Returns:
            List of :class:`SessionMeta` sorted by ``updated_at``
            descending (most recent first).
        """
        metas: list[SessionMeta] = []

        if not self._session_dir.exists():
            return metas

        for meta_path in self._session_dir.glob("*.meta.json"):
            try:
                meta = SessionMeta.model_validate_json(meta_path.read_text())
                metas.append(meta)
            except Exception:
                logger.warning("Skipping unreadable meta: %s", meta_path.name)

        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas

    def delete(self, session_id: str) -> bool:
        """Delete a session (from memory and disk).

        Returns:
            ``True`` if the session existed, ``False`` otherwise.
        """
        session = self._sessions.pop(session_id, None)
        if session is not None:
            session.writer.delete()
            logger.info("Deleted active session %s", session_id[:8])
            return True

        # Not in memory — try disk.
        writer = TranscriptWriter(self._session_dir, session_id)
        if writer.jsonl_path.exists() or writer.meta_path.exists():
            writer.delete()
            logger.info("Deleted persisted session %s", session_id[:8])
            return True

        return False

    @property
    def active_count(self) -> int:
        """Number of currently active (in-memory) sessions."""
        return len(self._sessions)

    def active_sessions(self) -> list[Session]:
        """Currently active (in-memory) sessions."""
        return list(self._sessions.values())

    # -- Internal ----------------------------------------------------

    def _build_orchestrator(
        self,
        cwd: Path | None = None,
        conversation: Conversation | None = None,
        session_id: str | None = None,
    ) -> Orchestrator:
        """Construct an Orchestrator with composed subsystems.

        Args:
            cwd: Working directory for context building.
            conversation: Pre-populated conversation for resume.
        """
        # Lazy imports to break circular dependencies.
        from daemon.engine.orchestrator.agent_factory import AgentFactory
        from daemon.engine.orchestrator.compactor import Compactor
        from daemon.engine.orchestrator.memory_extractor import MemoryExtractor
        from daemon.engine.orchestrator.memory_manager import MemoryManager
        from daemon.engine.orchestrator.orchestrator import Orchestrator as _Orchestrator
        from daemon.engine.orchestrator.plan_mode import PlanModeController
        from daemon.engine.orchestrator.prompt_builder import SystemPromptBuilder
        from daemon.engine.orchestrator.tool_executor import ToolExecutor
        from daemon.lifecycle import register_cleanup
        from daemon.tasks.store import TaskStore

        effective_cwd = (cwd or Path.cwd()).resolve()

        # Resolve initial permission mode.
        try:
            initial_mode = PermissionMode(self._config.permissions.mode)
        except ValueError:
            initial_mode = PermissionMode.PROMPT

        permission_engine = PermissionEngine(
            settings=self._permission_settings,
            mode=initial_mode,
        )

        # Context window from user config (may be None → auto-detect).
        provider_cfg = self._config.providers.get(self._config.default_provider)
        config_cw = provider_cfg.context_window if provider_cfg else None

        # Build subsystems.
        compactor = Compactor(
            context_window=config_cw or 0,
            hook_registry=self._ext_manager.hook_registry,
        )
        plan_mode = PlanModeController(
            permission_engine,
            session_dir=self._session_dir,
            session_id=session_id,
        )
        prompt_builder = SystemPromptBuilder(effective_cwd)
        task_store = TaskStore(self._session_dir, session_id) if session_id else None

        tool_executor = ToolExecutor(
            permission_engine=permission_engine,
            tool_registry=self._ext_manager.tool_registry,
            hook_registry=self._ext_manager.hook_registry,
            result_store=self._ext_manager.result_store,
            image_cache=self._image_cache,
            max_result_chars_override=self._config.tools.max_result_chars,
            plan_mode_controller=plan_mode,
            skill_setter=lambda p: setattr(prompt_builder, "_active_skill_prompt", p),
            task_store=task_store,
        )

        memory_manager: MemoryManager | None = None
        if self._memory_store is not None:
            memory_manager = MemoryManager(self._memory_store, self._config, effective_cwd)

        memory_extractor: MemoryExtractor | None = None
        if self._config.memory.auto_extract.enabled:
            memory_extractor = MemoryExtractor(
                self._config.memory.auto_extract,
                session_id=session_id,
            )

        orch = _Orchestrator(
            registry=self._registry,
            config=self._config,
            conversation=conversation,
            tool_executor=tool_executor,
            compactor=compactor,
            memory_manager=memory_manager,
            memory_extractor=memory_extractor,
            plan_mode=plan_mode,
            prompt_builder=prompt_builder,
            skill_registry=self._ext_manager.skill_registry,
            session_dir=self._session_dir,
            session_id=session_id,
            task_store=task_store,
        )

        # Wire the agent factory (needs the orchestrator reference).
        orch.agent_factory = AgentFactory(orch, self._config.agent, depth=0)

        # Register shutdown hooks.
        register_cleanup(orch.drain_pending_extractions)

        async def _save_access_counts() -> None:
            if self._memory_store is not None:
                self._memory_store.save_access_counts()

        register_cleanup(_save_access_counts)

        return orch


__all__ = [
    "Session",
    "SessionManager",
    "_rebuild_conversation",
]
