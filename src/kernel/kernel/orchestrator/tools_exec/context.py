"""Context builders for tool execution and authorization."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from kernel.orchestrator.types import OrchestratorDeps
    from kernel.tool_authz import AuthorizeContext, PermissionMode
    from kernel.tools import ToolManager
    from kernel.tools.context import ToolContext

logger = logging.getLogger(__name__)


class ToolContextMixin:
    """Build ToolContext and AuthorizeContext objects from executor state."""

    _agent_depth: int
    _agent_id: str | None
    _cwd: Path
    _deps: OrchestratorDeps
    _session_id: str
    _set_mode: Callable[[str], None] | None
    _set_plan_mode: Callable[[bool], None] | None
    _spawn_subagent: Any

    def _build_tool_context(self, tool_source: ToolManager | None) -> ToolContext:
        """Build the context object passed into a Tool implementation.

        Args:
            tool_source: ToolManager used to share file-state cache.

        Returns:
            ToolContext populated with session state and subsystem bridges.
        """
        from kernel.tools.context import ToolContext
        from kernel.tools.file_state import FileStateCache

        file_state = (
            tool_source.file_state()
            if tool_source is not None and hasattr(tool_source, "file_state")
            else FileStateCache()
        )
        return ToolContext(
            session_id=self._session_id,
            agent_depth=self._agent_depth,
            agent_id=self._agent_id,
            cwd=self._cwd,
            cancel_event=asyncio.Event(),
            file_state=file_state,
            tasks=self._deps.task_registry,
            set_plan_mode=self._set_plan_mode,
            set_mode=self._set_mode,
            interactive=self._session_can_prompt_user(),
            queue_reminders=self._deps.queue_reminders,
            spawn_subagent=self._spawn_subagent,
            deliver_cross_session=self._deps.deliver_cross_session,
            route_agent_message=self._deps.route_agent_message,
            schedule_manager=self._deps.schedule_manager,
            mcp_manager=getattr(self._deps, "mcp", None),
            git_manager=getattr(self._deps, "git", None),
            summarise=getattr(self._deps, "summarise", None),
            fire_hook=self._make_tool_hook_bridge(),
            module_table=getattr(self._deps, "module_table", None),
        )

    def _build_authorize_context(self, *, mode: PermissionMode) -> AuthorizeContext:
        """Build the context object passed into ToolAuthorizer.

        Args:
            mode: Current permission mode projected for authorization.

        Returns:
            AuthorizeContext for the current tool call.
        """
        from kernel.tool_authz import AuthorizeContext

        return AuthorizeContext(
            session_id=self._session_id,
            agent_depth=self._agent_depth,
            mode=mode,
            cwd=self._cwd,
            connection_auth=self._deps.connection_auth,
            should_avoid_prompts=self._session_should_avoid_prompts(),
            module_table=getattr(self._deps, "module_table", None),
        )

    def _session_can_prompt_user(self) -> bool:
        """Return False when the session has no interactive permission channel.

        Returns:
            ``True`` when tools may ask the user for interactive input.
        """
        return not self._session_should_avoid_prompts()

    def _session_should_avoid_prompts(self) -> bool:
        """Read the dynamic non-interactive-session guard.

        Returns:
            ``True`` when permission/user prompts should fail closed.
        """
        provider = self._deps.should_avoid_prompts_provider
        if provider is None:
            return False
        try:
            return bool(provider())
        except Exception:
            logger.debug("should_avoid_prompts_provider raised - defaulting False")
            return False

    def _make_tool_hook_bridge(self) -> Any:
        """Expose HookManager.fire through ToolContext without a tools import cycle.

        Returns:
            Async hook bridge callable, or ``None`` when hooks are unavailable.
        """
        hooks = self._deps.hooks
        if hooks is None:
            return None

        async def _fire_hook(_event: Any, event_ctx: Any) -> bool:
            """Forward a pre-built hook context to HookManager.

            Args:
                _event: Tool-supplied event marker kept for ToolContext signature.
                event_ctx: Hook context built by the tool.

            Returns:
                ``True`` when HookManager reports the event was blocked.
            """
            return await hooks.fire(event_ctx)

        return _fire_hook
