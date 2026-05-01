"""Rebuild a :class:`Conversation` from a JSONL entry chain.

Mirror of Claude Code's ``conversationRecovery.ts``.  Walks the
chain that :class:`TranscriptWriter.read_chain` returns and
converts each entry back into the universal :class:`Message`
format the providers expect.  Also implements the
``cancelled_tool_policy`` filter (acknowledge / hide / verbatim)
so resumed sessions can decide how the LLM perceives previously-
cancelled tool calls.

Filtering rules:

- Skip :class:`CompactBoundaryEntry` — the summary becomes a
  synthetic ``[Previous conversation summary]`` user message.
- Skip ``SessionMetaEntry`` (not part of conversation).
- A trailing user message without assistant reply is kept; the
  LLM will reply on the next query.
- Orphaned ``tool_use`` blocks are pruned via
  :meth:`Conversation.strip_orphaned_tool_calls` as a safety net.
"""

from __future__ import annotations

from typing import Any

from daemon.engine.conversation import Conversation
from daemon.providers.base import ImageContent, Message, TextContent, ToolUseContent
from daemon.sessions.entry import (
    AssistantMessageEntry,
    CompactBoundaryEntry,
    ToolCallEntry,
    UserMessageEntry,
)
from daemon.sessions.image_cache import ImageCache


def _restore_image_parts(
    serialised: list[dict[str, Any]] | None,
    image_cache: ImageCache | None,
) -> list[ImageContent] | None:
    """Rehydrate persisted image parts from the on-disk cache.

    Each serialised dict carries ``media_type`` + ``source_sha256``;
    base64 is loaded back from the cache.  Cache misses log a warning
    and drop the image from the rebuilt conversation (the text output
    is preserved so the LLM still sees the result).
    """
    if not serialised or image_cache is None:
        return None
    restored: list[ImageContent] = []
    for item in serialised:
        sha = item.get("source_sha256")
        media = item.get("media_type", "")
        if sha is None or not image_cache.has(sha, media):
            continue
        try:
            b64 = image_cache.load(sha, media)
        except FileNotFoundError:
            continue
        restored.append(
            ImageContent(
                media_type=media,
                data_base64=b64,
                source_sha256=sha,
                source_path=item.get("source_path"),
            )
        )
    return restored or None


def rebuild_conversation(
    chain: list[Any],
    cancelled_tool_policy: str = "acknowledge",
    image_cache: ImageCache | None = None,
) -> Conversation:
    """Reconstruct a :class:`Conversation` from a chain of entries.

    Cancelled-tool policy (see ``docs/plans/active/phase4-cancel-hardening.md``):

    - ``acknowledge`` (default) — synthetic entries pass through
      verbatim so the LLM sees ``<cancelled: …>`` markers.
    - ``hide`` — synthetic entries are dropped *and* the matching
      ``tool_use`` blocks are pruned from the assistant content,
      so the LLM sees a history where the cancelled attempt never
      happened.  JSONL still retains the synthetic entries for
      audit.
    - ``verbatim`` — synthetic entries are prefixed with phase +
      timestamp for debugging.

    Args:
        chain: Ordered list of entries (root → leaf).
        cancelled_tool_policy: One of ``acknowledge``/``hide``/``verbatim``.

    Returns:
        A populated :class:`Conversation`.
    """
    # Pre-compute the set of tool_call_ids whose ONLY result in the
    # chain is a synthetic cancel entry.  Under ``hide`` we drop
    # both these entries and the paired tool_use blocks.
    hidden_ids: set[str] = set()
    if cancelled_tool_policy == "hide":
        for entry in chain:
            if isinstance(entry, ToolCallEntry) and entry.synthetic:
                hidden_ids.add(entry.tool_call_id)

    conversation = Conversation()

    for entry in chain:
        if isinstance(entry, UserMessageEntry):
            conversation._append(Message.user(entry.content))

        elif isinstance(entry, AssistantMessageEntry):
            # Deserialise content blocks back to MessageContent.
            content: list[TextContent | ToolUseContent] = []
            for block in entry.content:
                btype = block.get("type")
                if btype == "text":
                    content.append(TextContent(text=block["text"]))
                elif btype == "tool_use":
                    tc_id = block["tool_call_id"]
                    if tc_id in hidden_ids:
                        # Policy=hide: drop the tool_use block so the
                        # LLM never sees the attempt.
                        continue
                    content.append(
                        ToolUseContent(
                            tool_call_id=tc_id,
                            name=block["name"],
                            arguments=block.get("arguments", {}),
                        )
                    )
            if content:
                conversation._append(Message(role="assistant", content=content))  # type: ignore[arg-type]

        elif isinstance(entry, ToolCallEntry):
            if entry.synthetic:
                if cancelled_tool_policy == "hide":
                    # Drop synthetic entry entirely.  Its paired
                    # tool_use was already filtered above; the LLM
                    # sees a clean history.
                    continue
                if cancelled_tool_policy == "verbatim":
                    phase = entry.cancel_phase or "unknown"
                    output = f"<cancelled at {entry.timestamp} in phase={phase}> {entry.output}"
                    conversation._append(
                        Message.tool_result(entry.tool_call_id, output, entry.is_error)
                    )
                    continue
                # acknowledge — fall through to the default append.
            conversation._append(
                Message.tool_result(
                    entry.tool_call_id,
                    entry.output,
                    entry.is_error,
                    image_parts=_restore_image_parts(entry.image_parts, image_cache),
                ),
            )

        elif isinstance(entry, CompactBoundaryEntry):
            # Inject the compression summary as a system-like user
            # message so the LLM has context from before compaction.
            conversation._append(Message.user(f"[Previous conversation summary]\n{entry.summary}"))

        # SessionMetaEntry — skip (not part of conversation).

    # Safety net: strip any lingering orphaned tool_use blocks.
    # With the policy filter above this is usually a no-op, but
    # pre-existing JSONL may have interior orphans that predate
    # the cancel finalizer.
    conversation.strip_orphaned_tool_calls_sync()

    return conversation


__all__ = ["rebuild_conversation"]
