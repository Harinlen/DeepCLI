"""ConversationHistory in-memory state for one Orchestrator."""

from __future__ import annotations

import logging

from kernel.llm.types import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
    UserMessage,
)
from kernel.orchestrator.history.pairs import pending_tool_use_ids
from kernel.orchestrator.history.thinking import assemble_thinking
from kernel.orchestrator.history.tokens import estimate_tokens_for
from kernel.orchestrator.tool_kinds import ToolKind

UserContent = TextContent | ImageContent | ToolResultContent

logger = logging.getLogger(__name__)


class ConversationHistory:
    """In-memory conversation history with token-count tracking.

    The object owns provider-neutral message order plus side metadata, such as
    tool kinds, that is needed by compaction but should not be serialized into
    LLM messages.
    """

    def __init__(self, initial_messages: list[Message] | None = None) -> None:
        """Create history from optional restored messages.

        Args:
            initial_messages: Existing provider-neutral messages for a resumed
                session.
        """
        self._messages: list[Message] = list(initial_messages or [])
        self._token_count: int = estimate_tokens_for(self._messages)
        self._tool_kinds: dict[str, ToolKind] = {}

    @property
    def messages(self) -> list[Message]:
        """Current message list passed verbatim to the LLM provider.

        Returns:
            Mutable message list owned by the history object.
        """
        return self._messages

    @property
    def token_count(self) -> int:
        """Best-known token count.

        Returns:
            Provider usage when available, otherwise local estimate.
        """
        return self._token_count

    def append_user(self, content: list[TextContent]) -> None:
        """Append a user turn.

        Args:
            content: Text blocks for the normalized user prompt.

        Returns:
            ``None``.
        """
        msg = UserMessage(content=list(content))
        self._messages.append(msg)
        self._token_count += estimate_tokens_for([msg])

    def append_assistant(
        self,
        *,
        text: str,
        thoughts: list[object],
        tool_calls: list[ToolUseContent],
    ) -> None:
        """Append the LLM assistant turn.

        Args:
            text: Visible assistant text accumulated from stream chunks.
            thoughts: Provider thought chunks to assemble for replay.
            tool_calls: Tool uses emitted by the model.

        Returns:
            ``None``.
        """
        content: list[TextContent | ToolUseContent | ThinkingContent] = []
        thinking = assemble_thinking(thoughts)
        if thinking is not None:
            content.append(thinking)
        if text:
            content.append(TextContent(text=text))
        content.extend(tool_calls)

        if not content:
            logger.debug("append_assistant: empty content, skipping")
            return

        msg = AssistantMessage(content=content)
        self._messages.append(msg)
        self._token_count += estimate_tokens_for([msg])

    def append_tool_results(self, results: list[ToolResultContent]) -> None:
        """Append tool results as a user-role message.

        Args:
            results: Tool result blocks corresponding to the last assistant turn.

        Returns:
            ``None``.
        """
        if not results:
            return
        content: list[UserContent] = list(results)
        msg = UserMessage(content=content)
        self._messages.append(msg)
        self._token_count += estimate_tokens_for([msg])

    def record_tool_kind(self, tool_use_id: str, kind: ToolKind) -> None:
        """Record the ToolKind for a tool_use_id.

        Args:
            tool_use_id: Provider tool-use id.
            kind: Semantic kind reported by the Tool implementation.

        Returns:
            ``None``.
        """
        self._tool_kinds[tool_use_id] = kind

    def tool_kind_for(self, tool_use_id: str) -> ToolKind | None:
        """Look up the ToolKind for a tool_use_id.

        Args:
            tool_use_id: Provider tool-use id.

        Returns:
            Recorded tool kind, or ``None`` when no tool metadata is known.
        """
        return self._tool_kinds.get(tool_use_id)

    def pop_last_assistant(self) -> bool:
        """Remove the most-recent AssistantMessage, if present.

        Returns:
            ``True`` when an assistant message was removed.
        """
        if self._messages and isinstance(self._messages[-1], AssistantMessage):
            self._messages.pop()
            self._token_count = estimate_tokens_for(self._messages)
            return True
        return False

    def pending_tool_use_ids(self) -> list[str]:
        """Return pending tool_use IDs from the last assistant message.

        Returns:
            Tool-use ids that still need matching results.
        """
        return pending_tool_use_ids(self._messages)

    def update_token_count(self, input_tokens: int, output_tokens: int) -> None:
        """Replace the running estimate with provider usage.

        Args:
            input_tokens: Provider-reported prompt tokens.
            output_tokens: Provider-reported completion tokens.

        Returns:
            ``None``.
        """
        if input_tokens > 0:
            self._token_count = input_tokens + output_tokens

    def find_compaction_boundary(self, keep_recent_turns: int = 5) -> int:
        """Return the first message index to keep during compaction.

        Args:
            keep_recent_turns: Number of recent user turns preserved verbatim.

        Returns:
            Message index where the un-compacted suffix begins, or ``0`` when
            there is not enough history to compact.
        """
        user_indices = [i for i, msg in enumerate(self._messages) if isinstance(msg, UserMessage)]
        if len(user_indices) <= keep_recent_turns:
            return 0
        return user_indices[-keep_recent_turns]

    def replace_with_compacted(
        self,
        summary: str,
        boundary: int,
        summary_header: str | None = None,
    ) -> None:
        """Replace messages before ``boundary`` with a summary message.

        Args:
            summary: Plain-text summary from the compactor.
            boundary: First original message index to keep.
            summary_header: Optional pre-rendered summary prompt header.

        Returns:
            ``None``.
        """
        kept = self._messages[boundary:]
        header_text = summary_header or f"Prior conversation summary:\n{summary}"
        summary_msg: Message = UserMessage(content=[TextContent(text=header_text)])
        self._messages = [summary_msg, *kept]
        self._token_count = estimate_tokens_for(self._messages)

    def _estimate_tokens_for(self, messages: list[Message]) -> int:
        """Compatibility wrapper for compaction helpers.

        Args:
            messages: Message list to estimate.

        Returns:
            Rough token estimate for ``messages``.
        """
        return estimate_tokens_for(messages)
