"""Persist ``SessionEvent`` rows and large tool-result content.

``_write_event`` is the single chokepoint for appending to the session
log: it stamps each event with the canonical context (session id, cwd,
parent event, kernel version) and updates the in-memory ``Session`` head.
``_maybe_spill`` keeps the log row small by externalising oversized tool
output to a sibling file.
"""

from __future__ import annotations

import builtins
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from kernel.protocol.acp.schemas.updates import CurrentModeUpdate
from kernel.session._shared.base import _SessionMixinBase
from kernel.session.events import KERNEL_VERSION, ModeChangedEvent
from kernel.session.models import TokenUsageUpdate
from kernel.session.runtime.state import Session

UTC = timezone.utc
logger = logging.getLogger("kernel.session")


class SessionEventWriterMixin(_SessionMixinBase):
    """Single chokepoint for appending session events and spilling tool blobs."""

    async def _drain_pending_mode_changes(self, session: Session) -> None:
        """Flush mode changes queued by the sync ``_set_mode`` closure.

        Each pending ``(from, to)`` pair becomes one ``ModeChangedEvent``
        plus one ``CurrentModeUpdate`` broadcast.

        Args:
            session: Session whose ``pending_mode_changes`` is consumed.
        """
        if not session.pending_mode_changes:
            return
        changes = list(session.pending_mode_changes)
        session.pending_mode_changes.clear()
        for from_mode, to_mode in changes:
            await self._write_event(
                session,
                ModeChangedEvent,
                mode_id=to_mode,
                from_mode=from_mode,
            )
            await self._broadcast(session, CurrentModeUpdate(mode_id=to_mode))

    async def _write_event(
        self,
        session: Session,
        event_cls: type,
        *,
        tokens: TokenUsageUpdate | None = None,
        **kwargs: Any,
    ) -> str:
        """Build and persist one session event.

        Args:
            session: Owning session (provides context fields and last_event_id).
            event_cls: Concrete event class to instantiate.
            tokens: When provided, passed to ``store.append_event`` to
                atomically update the sessions row token counters.
            **kwargs: Event-specific fields forwarded to the constructor.

        Returns:
            The new ``event_id`` string.
        """
        event_id = "ev_" + uuid.uuid4().hex
        now = datetime.now(UTC)
        event = event_cls(
            event_id=event_id,
            parent_id=session.last_event_id,
            timestamp=now,
            session_id=session.session_id,
            agent_depth=0,
            kernel_version=KERNEL_VERSION,
            cwd=str(session.cwd),
            git_branch=session.git_branch,
            **kwargs,
        )
        await self._store.append_event(session.session_id, event, tokens=tokens)
        session.last_event_id = event_id
        session.updated_at = now
        return event_id

    @staticmethod
    def _blocks_to_raw(blocks: "builtins.list[Any]") -> "builtins.list[dict[str, Any]]":
        """Convert a mixed list of Pydantic models / dicts to JSON-friendly dicts.

        Args:
            blocks: Heterogeneous content blocks — Pydantic models, plain
                dicts, or other objects.

        Returns:
            One dict per item the log can round-trip; unrecognised items
            are dropped silently.
        """
        raw_blocks: list[dict[str, Any]] = []
        for block in blocks:
            if hasattr(block, "model_dump"):
                raw_blocks.append(block.model_dump())
            elif isinstance(block, dict):
                raw_blocks.append(block)
        return raw_blocks

    def _maybe_spill(
        self,
        session: "Session",
        tool_call_id: str,
        content_raw: "builtins.list[dict[str, Any]]",
    ) -> "builtins.list[dict[str, Any]]":
        """Externalise oversized tool output to a sidecar file.

        Args:
            session: Owning session — the sidecar is scoped to its dir.
            tool_call_id: Tool call id, used for log context on failure.
            content_raw: Persisted-shape blocks that may need spilling.

        Returns:
            ``content_raw`` unchanged when its inline text fits under
            ``tool_result_inline_limit``; otherwise a single ``spilled``
            block referencing the sidecar.  Spillover write failures
            fall back to inlining so the log still records the result.
        """
        inline_text = " ".join(
            block.get("text", "") for block in content_raw if block.get("type") == "text"
        )
        if len(inline_text.encode()) <= self._flags.tool_result_inline_limit:
            return content_raw
        try:
            relative_path, _result_hash = self._store.write_spilled(session.session_id, inline_text)
            return [
                {
                    "type": "spilled",
                    "path": relative_path,
                    "size": len(inline_text),
                    "preview": inline_text[:200],
                }
            ]
        except Exception:
            logger.exception(
                "session=%s tool=%s: spillover write failed — inlining",
                session.session_id,
                tool_call_id,
            )
            return content_raw
