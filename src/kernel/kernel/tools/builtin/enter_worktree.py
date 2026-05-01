"""EnterWorktreeTool — create a git worktree for isolated development.

Registered dynamically by GitManager._sync_tools() in the deferred layer.
CC reference: ``src/tools/EnterWorktreeTool/EnterWorktreeTool.ts``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any, ClassVar

from kernel.git.types import WorktreeSession
from kernel.git.worktree import (
    create_worktree,
    find_git_root,
    setup_sparse_checkout,
    validate_slug,
)
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


class EnterWorktreeTool(Tool[dict[str, Any], dict[str, Any]]):
    """Create a git worktree and switch the session cwd into it."""

    name = "EnterWorktree"
    description_key = "tools/enter_worktree"
    description = "Create an isolated worktree and switch the session into it."
    kind = ToolKind.execute
    should_defer = True
    search_hint = "worktree branch isolate parallel development git"

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "description": (
                    "Name for the worktree (1-64 chars, [a-zA-Z0-9._-], no '..' segments)"
                ),
            },
            "sparse_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of directories to sparse-checkout. "
                    "If omitted, the full repo is checked out."
                ),
            },
        },
        "required": ["slug"],
    }

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="medium",
            default_decision="ask",
            reason=f"creates git worktree '{input.get('slug', '?')}'",
        )

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        slug = input["slug"]
        sparse_paths: list[str] | None = input.get("sparse_paths")
        git_mgr = ctx.git_manager

        # Git path: full git-worktree isolation (branch + sparse checkout).
        if git_mgr is not None and git_mgr.available:
            async for ev in self._call_git(slug, sparse_paths, ctx, git_mgr):
                yield ev
            return

        # Non-git path (CC parity): fire WORKTREE_CREATE hook so a user
        # handler can perform VCS-agnostic isolation (e.g. jj, fossil,
        # plain-directory snapshot).  When no handler is configured we
        # emit CC's exact error message so the LLM can surface it to the
        # user.
        async for ev in self._call_hook(slug, ctx):
            yield ev

    async def _call_git(
        self,
        slug: str,
        sparse_paths: list[str] | None,
        ctx: Any,
        git_mgr: Any,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        """Original git-backed worktree creation path."""
        # 1. Validate slug.
        validate_slug(slug)

        # 2. Must not already be in a worktree session.
        if git_mgr.get_worktree(ctx.session_id) is not None:
            raise ToolInputError("already in a worktree session")

        # 3. Find git root.
        git_root = await find_git_root(git_mgr, ctx.cwd)

        # 4. Create worktree.
        worktree_path, branch = await create_worktree(git_mgr, git_root, slug)

        # 5. Optional sparse checkout.
        if sparse_paths:
            await setup_sparse_checkout(git_mgr, worktree_path, sparse_paths)

        # 6. Register in GitManager (memory + SQLite).
        ws = WorktreeSession(
            session_id=ctx.session_id,
            original_cwd=ctx.cwd,
            worktree_path=worktree_path,
            worktree_branch=branch,
            slug=slug,
            created_at=datetime.now(timezone.utc),
        )
        await git_mgr.register_worktree(ws)

        # 7. Return result with context_modifier to switch cwd.
        def modifier(old_ctx: Any) -> Any:
            return dataclasses.replace(old_ctx, cwd=worktree_path)

        msg = f"Entered worktree at {worktree_path} on branch {branch}"
        if sparse_paths:
            msg += f" (sparse: {', '.join(sparse_paths)})"

        yield ToolCallResult(
            data={
                "worktree_path": str(worktree_path),
                "branch": branch,
                "sparse_paths": sparse_paths,
            },
            llm_content=[TextBlock(text=msg)],
            display=TextDisplay(text=f"Entered worktree: {worktree_path}"),
            context_modifier=modifier,
        )

    async def _call_hook(
        self,
        slug: str,
        ctx: Any,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        """Hook-backed fallback for non-git projects (CC parity)."""
        from pathlib import Path

        from kernel.hooks.types import AmbientContext, HookEvent, HookEventCtx

        validate_slug(slug)

        fire = getattr(ctx, "fire_hook", None)
        if fire is None:
            raise ToolInputError(
                "Cannot create a worktree: not in a git repository and no "
                "WorktreeCreate hooks are configured. Configure "
                "WorktreeCreate/WorktreeRemove hooks in settings to use "
                "worktree isolation with other VCS systems."
            )

        import time as _time

        event_ctx = HookEventCtx(
            event=HookEvent.WORKTREE_CREATE,
            ambient=AmbientContext(
                session_id=ctx.session_id,
                cwd=ctx.cwd,
                agent_depth=ctx.agent_depth,
                mode="default",
                timestamp=_time.time(),
            ),
            worktree_slug=slug,
        )
        await fire(HookEvent.WORKTREE_CREATE, event_ctx)

        if not event_ctx.worktree_handled or not event_ctx.worktree_path:
            raise ToolInputError(
                "Cannot create a worktree: not in a git repository and no "
                "WorktreeCreate hooks are configured. Configure "
                "WorktreeCreate/WorktreeRemove hooks in settings to use "
                "worktree isolation with other VCS systems."
            )

        new_cwd = Path(event_ctx.worktree_path)

        def modifier(old_ctx: Any) -> Any:
            return dataclasses.replace(old_ctx, cwd=new_cwd)

        msg = f"Entered hook-managed worktree at {new_cwd}"
        yield ToolCallResult(
            data={
                "worktree_path": str(new_cwd),
                "branch": None,
                "backend": "hook",
            },
            llm_content=[TextBlock(text=msg)],
            display=TextDisplay(text=msg),
            context_modifier=modifier,
        )
