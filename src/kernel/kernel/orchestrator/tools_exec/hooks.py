"""Hook bridge used by tool execution."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from kernel.hooks import AmbientContext, HookEvent, HookEventCtx

if TYPE_CHECKING:
    from kernel.orchestrator.types import OrchestratorDeps


class ToolHookMixin:
    """Fire tool lifecycle hooks and drain reminder messages."""

    _session_id: str
    _cwd: Path
    _agent_depth: int
    _deps: OrchestratorDeps

    async def _fire_hook(
        self,
        *,
        event: HookEvent,
        mode: Literal["default", "plan", "bypass"],
        tool_name: str | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_output: str | None = None,
        error_message: str | None = None,
    ) -> tuple[bool, HookEventCtx]:
        """Fire ``event`` through ``deps.hooks`` and queue reminders.

        Args:
            event: Tool lifecycle hook event.
            mode: Current projected permission mode.
            tool_name: Optional tool name for hook matching.
            tool_input: Optional effective tool input.
            tool_output: Optional text output for post-use hooks.
            error_message: Optional failure message for post-failure hooks.

        Returns:
            ``(blocked, hook_context)``.
        """
        ambient = AmbientContext(
            session_id=self._session_id,
            cwd=self._cwd,
            agent_depth=self._agent_depth,
            mode=mode,
            timestamp=time.time(),
        )
        ctx = HookEventCtx(
            event=event,
            ambient=ambient,
            tool_name=tool_name,
            tool_input=dict(tool_input) if tool_input else {},
            tool_output=tool_output,
            error_message=error_message,
        )
        hooks = self._deps.hooks
        if hooks is None:
            return False, ctx

        blocked = await hooks.fire(ctx)
        drain = self._deps.queue_reminders
        if drain is not None and ctx.messages:
            drain(list(ctx.messages))
        return blocked, ctx
