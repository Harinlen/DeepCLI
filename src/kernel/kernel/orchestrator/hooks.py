"""Query-level hook bridge for Orchestrator."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from kernel.hooks.types import AmbientContext, HookEvent, HookEventCtx
from kernel.orchestrator.types import OrchestratorDeps


async def fire_query_hook(
    *,
    deps: OrchestratorDeps,
    session_id: str,
    cwd: Path,
    depth: int,
    mode: Literal["default", "plan", "bypass", "accept_edits", "auto", "dont_ask"],
    event: HookEvent,
    user_text: str | None = None,
    message_count: int | None = None,
    token_estimate: int | None = None,
    stop_reason: str | None = None,
) -> tuple[bool, HookEventCtx]:
    """Fire a query-level hook and queue reminder messages.

    Args:
        deps: Orchestrator dependency bundle containing optional HookManager.
        session_id: Session that owns the query.
        cwd: Current working directory at hook time.
        depth: Root/sub-agent depth used by hook policy.
        mode: Current permission mode.
        event: Hook event being fired.
        user_text: User text for prompt-submit hooks.
        message_count: Conversation length for stop hooks.
        token_estimate: Current token estimate for stop hooks.
        stop_reason: Provider/query stop reason for stop hooks.

    Returns:
        ``(blocked, ctx)`` where ``ctx`` includes any hook mutations/messages.
    """
    ctx = HookEventCtx(
        event=event,
        ambient=AmbientContext(
            session_id=session_id,
            cwd=cwd,
            agent_depth=depth,
            mode=mode,
            timestamp=time.time(),
        ),
        user_text=user_text,
        message_count=message_count,
        token_estimate=token_estimate,
        stop_reason=stop_reason,
    )
    if deps.hooks is None:
        return False, ctx
    blocked = await deps.hooks.fire(ctx)
    if deps.queue_reminders is not None and ctx.messages:
        # Hook messages are delivered as system reminders on the next turn so the
        # current provider stream is never mutated mid-flight.
        deps.queue_reminders(list(ctx.messages))
    return blocked, ctx
