"""Factory for building child Orchestrator instances (Phase 5.2).

Given a parent orchestrator's infrastructure (provider registry,
config, permission settings), builds a child with:

- **Fresh** conversation (no message inheritance).
- **Filtered** tool registry (caller-specified subset, or full
  minus ``agent`` at max depth).
- **Shared** config (read-only reference).
- **Shared** provider registry.
- **Own** permission engine (shared rules, mode may be narrowed).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from daemon.config.schema import AgentRuntimeConfig
from daemon.engine.conversation import Conversation
from daemon.extensions.tools.registry import ToolRegistry
from daemon.permissions.engine import PermissionEngine
from daemon.permissions.modes import PermissionMode

if TYPE_CHECKING:
    from daemon.engine.orchestrator.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class AgentFactory:
    """Builds child :class:`Orchestrator` instances for sub-agent calls.

    One ``AgentFactory`` is attached to each orchestrator.  It holds
    references to the parent's shared infrastructure and the current
    nesting depth.  When ``build_child()`` is called, it creates a
    new orchestrator with isolated conversation and (optionally)
    filtered tools.

    Args:
        parent: The parent orchestrator (for accessing shared state).
        agent_config: Resolved agent settings (depth, timeout, etc.).
        depth: Current nesting depth (0 = root agent).
    """

    def __init__(
        self,
        parent: Orchestrator,
        agent_config: AgentRuntimeConfig,
        depth: int = 0,
    ) -> None:
        self._parent = parent
        self._config = agent_config
        self._depth = depth

    @property
    def depth(self) -> int:
        """Current nesting depth (0 = root)."""
        return self._depth

    @property
    def max_depth(self) -> int:
        """Maximum allowed depth from config."""
        return self._config.max_depth

    @property
    def can_spawn(self) -> bool:
        """Whether this agent can spawn children."""
        return self._depth < self._config.max_depth

    @property
    def timeout_seconds(self) -> int:
        """Per-agent execution timeout."""
        return self._config.timeout_seconds

    @property
    def max_concurrent(self) -> int:
        """Maximum concurrent sub-agents."""
        return self._config.max_concurrent

    def build_child(
        self,
        *,
        tools: list[str] | None = None,
        permission_mode: PermissionMode | None = None,
        cwd: Path | None = None,
        on_entry: Any = None,
        session_dir: Path | None = None,
        session_id: str | None = None,
    ) -> Orchestrator:
        """Create a child orchestrator with isolated state.

        Args:
            tools: Tool names to include.  ``None`` inherits all
                parent tools.  The ``agent`` tool is automatically
                excluded at max depth.
            permission_mode: Permission mode for the child.
                Defaults to parent's current mode.
            cwd: Working directory.  Defaults to parent's cwd.
            on_entry: Transcript writer callback for the child.
            session_dir: Session directory (for plan file persistence).
            session_id: Session ID (for plan file naming).

        Returns:
            A new :class:`Orchestrator` ready for ``query()``.
        """
        from daemon.engine.orchestrator.compactor import Compactor
        from daemon.engine.orchestrator.memory_manager import MemoryManager
        from daemon.engine.orchestrator.orchestrator import Orchestrator
        from daemon.engine.orchestrator.plan_mode import PlanModeController
        from daemon.engine.orchestrator.prompt_builder import SystemPromptBuilder
        from daemon.engine.orchestrator.tool_executor import ToolExecutor

        parent = self._parent
        child_depth = self._depth + 1

        # Build filtered tool registry.
        child_tool_registry = _build_child_tool_registry(
            parent_registry=parent.tool_executor.tool_registry,
            allowed_names=set(tools) if tools else None,
            child_depth=child_depth,
            max_depth=self._config.max_depth,
        )

        # Child permission engine: shared rules, mode may be narrowed.
        child_mode = permission_mode or parent.permission_engine.mode
        child_engine = PermissionEngine(
            settings=parent.permission_engine.settings,
            mode=child_mode,
        )

        # Build child subsystems.
        effective_cwd = cwd or parent.prompt_builder._cwd
        child_compactor = Compactor(
            hook_registry=parent.tool_executor._hook_registry,
        )
        child_plan_mode = PlanModeController(
            child_engine,
            session_dir=session_dir,
            session_id=session_id,
        )
        child_prompt_builder = SystemPromptBuilder(effective_cwd)
        child_tool_exec = ToolExecutor(
            permission_engine=child_engine,
            tool_registry=child_tool_registry,
            hook_registry=parent.tool_executor._hook_registry,
            result_store=parent.tool_executor._result_store,
            image_cache=parent.tool_executor._image_cache,
            max_result_chars_override=parent.tool_executor._max_result_chars_override,
            plan_mode_controller=child_plan_mode,
            skill_setter=lambda p: setattr(child_prompt_builder, "_active_skill_prompt", p),
        )

        # Child memory: share parent's stores.
        child_memory: MemoryManager | None = None
        if parent.memory_manager is not None:
            child_memory = MemoryManager(
                memory_store=parent.memory_manager.memory_store,
                config=parent._config,
                cwd=effective_cwd,
            )

        # Child agent factory (decremented depth).
        child_factory = AgentFactory(
            parent=None,  # type: ignore[arg-type]  # Replaced below.
            agent_config=self._config,
            depth=child_depth,
        )

        child = Orchestrator(
            registry=parent._registry,
            config=parent._config,
            conversation=Conversation(),
            tool_executor=child_tool_exec,
            compactor=child_compactor,
            memory_manager=child_memory,
            memory_extractor=None,  # Children don't extract memories.
            plan_mode=child_plan_mode,
            prompt_builder=child_prompt_builder,
            skill_registry=parent._skill_registry,
            on_entry=on_entry,
            session_dir=session_dir,
            session_id=session_id,
        )

        # Wire the child factory's parent reference.
        child_factory._parent = child
        child.agent_factory = child_factory

        return child


def _build_child_tool_registry(
    parent_registry: ToolRegistry,
    allowed_names: set[str] | None,
    child_depth: int,
    max_depth: int,
) -> ToolRegistry:
    """Build a tool registry for a child agent.

    1. If ``allowed_names`` is ``None``, clone all parent tools.
    2. Otherwise, clone only the named tools.
    3. At max depth, remove the ``agent`` tool (prevent infinite
       recursion).
    """
    child = parent_registry.clone(allowed_names)
    if child_depth >= max_depth:
        child.unregister("agent")
    return child
