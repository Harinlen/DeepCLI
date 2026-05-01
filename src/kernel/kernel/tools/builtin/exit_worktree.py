"""ExitWorktreeTool — exit a git worktree session.

Registered dynamically by GitManager._sync_tools() in the deferred layer.
CC reference: ``src/tools/ExitWorktreeTool/ExitWorktreeTool.ts``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from kernel.git.worktree import count_changes, remove_worktree
from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.tool import Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)


class ExitWorktreeTool(Tool[dict[str, Any], dict[str, Any]]):
    """Exit a git worktree session (keep or remove the worktree)."""

    name = "ExitWorktree"
    description_key = "tools/exit_worktree"
    description = "Exit the worktree created by EnterWorktree."
    kind = ToolKind.execute
    should_defer = True
    search_hint = "worktree exit leave return original directory git"

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["keep", "remove"],
                "description": "Whether to keep or remove the worktree.",
            },
            "discard_changes": {
                "type": "boolean",
                "description": (
                    "Force remove even with uncommitted changes. Only used when action is 'remove'."
                ),
            },
        },
        "required": ["action"],
    }

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        action = input.get("action", "keep")
        if action == "remove":
            return PermissionSuggestion(
                risk="high",
                default_decision="ask",
                reason="removes git worktree and deletes branch",
            )
        return PermissionSuggestion(
            risk="low",
            default_decision="allow",
            reason="keeps worktree on disk, only restores cwd",
        )

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        action = input["action"]
        discard = input.get("discard_changes", False)
        git_mgr = ctx.git_manager

        # Git path: full git-worktree removal with change detection.
        if git_mgr is not None and git_mgr.available:
            async for ev in self._call_git(action, discard, ctx, git_mgr):
                yield ev
            return

        # Non-git path (CC parity): fire WORKTREE_REMOVE hook.
        async for ev in self._call_hook(action, ctx):
            yield ev

    async def _call_git(
        self,
        action: str,
        discard: bool,
        ctx: Any,
        git_mgr: Any,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        """Original git-backed worktree exit path."""
        # 1. Must be in a worktree session.
        ws = git_mgr.get_worktree(ctx.session_id)
        if ws is None:
            raise ToolInputError("not in a worktree session")

        # 2. Check for uncommitted changes (remove only).
        if action == "remove" and not discard:
            changes = await count_changes(git_mgr, ws.worktree_path)
            if changes > 0:
                raise ToolInputError(
                    f"worktree has {changes} uncommitted change(s). "
                    "Set discard_changes=true to force remove, or use "
                    "action='keep' to preserve the worktree."
                )

        # 3. Execute.
        if action == "remove":
            await remove_worktree(git_mgr, ws)

        # 4. Unregister from GitManager (memory + SQLite).
        await git_mgr.unregister_worktree(ctx.session_id)

        # 5. Restore cwd via context_modifier.
        original_cwd = ws.original_cwd

        def modifier(old_ctx: Any) -> Any:
            return dataclasses.replace(old_ctx, cwd=original_cwd)

        msg = (
            f"Removed worktree at {ws.worktree_path}"
            if action == "remove"
            else f"Exited worktree (kept at {ws.worktree_path})"
        )

        yield ToolCallResult(
            data={
                "action": action,
                "original_cwd": str(original_cwd),
            },
            llm_content=[TextBlock(text=msg)],
            display=TextDisplay(text=msg),
            context_modifier=modifier,
        )

    async def _call_hook(
        self,
        action: str,
        ctx: Any,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        """Hook-backed fallback for non-git projects (CC parity)."""
        import time as _time

        from kernel.hooks.types import AmbientContext, HookEvent, HookEventCtx

        fire = getattr(ctx, "fire_hook", None)
        if fire is None:
            raise ToolInputError(
                "not in a worktree session and no WorktreeRemove hook is "
                "configured."
            )

        event_ctx = HookEventCtx(
            event=HookEvent.WORKTREE_REMOVE,
            ambient=AmbientContext(
                session_id=ctx.session_id,
                cwd=ctx.cwd,
                agent_depth=ctx.agent_depth,
                mode="default",
                timestamp=_time.time(),
            ),
            worktree_path=str(ctx.cwd),
        )
        # ``action`` is echoed through tool_input for the handler to
        # branch on (keep vs remove).
        event_ctx.tool_input = {"action": action}
        await fire(HookEvent.WORKTREE_REMOVE, event_ctx)

        if not event_ctx.worktree_handled:
            raise ToolInputError(
                "No WorktreeRemove hook handled the request — either git is "
                "unavailable or the handler did not acknowledge. Aborting."
            )

        msg = f"Hook-managed worktree {action}d at {event_ctx.worktree_path}"
        yield ToolCallResult(
            data={
                "action": action,
                "backend": "hook",
            },
            llm_content=[TextBlock(text=msg)],
            display=TextDisplay(text=msg),
        )
