"""Shared types between the LLM layer and Provider layer.

This module is the single source of truth for all types that cross the
LLMManager ↔ Provider boundary.  Both ``kernel.llm`` and
``kernel.llm_provider`` import from here; neither imports from the other's
types module.

Import rule
-----------
``kernel.llm_provider.*`` may import from ``kernel.llm.types``.
``kernel.llm.*`` may import from ``kernel.llm_provider.*``.
``kernel.llm.types`` imports nothing from the kernel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


# ---------------------------------------------------------------------------
# LLMChunk — stream output (Provider → LLMManager → Orchestrator)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextChunk:
    """Streaming text delta from the model."""

    content: str


@dataclass(frozen=True)
class ThoughtChunk:
    """Streaming extended-thinking / reasoning delta (Anthropic-specific).

    ``signature`` is the verification token emitted at end of a thinking
    block; it is empty for mid-block deltas.
    """

    content: str
    signature: str = ""


@dataclass(frozen=True)
class ToolUseChunk:
    """Complete tool call, emitted once at ``content_block_stop``.

    Providers accumulate ``input_json_delta`` fragments internally and
    emit this chunk only when input JSON is fully received.  The
    Orchestrator always gets a fully-parsed ``input`` dict.
    """

    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class UsageChunk:
    """Token usage, emitted exactly once after the stream ends."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    stop_reason: str | None = None
    """Provider-level stop reason (e.g. ``"end_turn"``, ``"max_tokens"``,
    ``"tool_use"``).  ``None`` when the provider does not report one."""


@dataclass(frozen=True)
class StreamError:
    """Recoverable provider error surfaced as a chunk, not raised.

    Transient failures (rate limits, temporary API outages) are yielded
    so the Orchestrator can decide whether to retry.  Unrecoverable errors
    (bad config, auth failures) raise ``ProviderError`` instead.
    """

    message: str
    code: str | None = None


type LLMChunk = TextChunk | ThoughtChunk | ToolUseChunk | UsageChunk | StreamError


# ---------------------------------------------------------------------------
# PromptSection — structured system prompt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptSection:
    """One section of the assembled system prompt.

    ``cache=True`` marks this section for prompt caching.  Anthropic
    providers add ``cache_control: {type: ephemeral}``; others ignore it.
    """

    text: str
    cache: bool = False


# ---------------------------------------------------------------------------
# Message — conversation history
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextContent:
    text: str
    type: Literal["text"] = "text"


@dataclass(frozen=True)
class ImageContent:
    media_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
    data_base64: str
    type: Literal["image"] = "image"


@dataclass(frozen=True)
class ToolUseContent:
    id: str
    name: str
    input: dict[str, Any]
    type: Literal["tool_use"] = "tool_use"


@dataclass(frozen=True)
class ToolResultContent:
    """Tool result, embedded in a UserMessage (Anthropic style).

    Providers that use a separate ``tool`` role (OpenAI) convert this
    during format translation — LLMManager and Orchestrator never need
    to know.
    """

    tool_use_id: str
    content: str | list[TextContent | ImageContent]
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"


@dataclass(frozen=True)
class UserMessage:
    content: list[TextContent | ImageContent | ToolResultContent]
    role: Literal["user"] = "user"


@dataclass(frozen=True)
class ThinkingContent:
    """Anthropic extended-thinking block, persisted in conversation history.

    The Anthropic API requires that thinking blocks from an assistant turn
    are passed back verbatim (including ``signature``) in subsequent requests.
    Omitting them causes a 422 error.  Other providers never produce this type.

    Assembly: the provider yields multiple ``ThoughtChunk`` events per block —
    ``content`` deltas first, then one ``ThoughtChunk(content="", signature=...)``
    at the end.  ``ConversationHistory.append_assistant()`` joins all content
    deltas and extracts the signature to build this object.
    """

    thinking: str
    signature: str
    type: Literal["thinking"] = "thinking"


@dataclass(frozen=True)
class AssistantMessage:
    content: list[TextContent | ToolUseContent | ThinkingContent]
    role: Literal["assistant"] = "assistant"


Message = UserMessage | AssistantMessage


# ---------------------------------------------------------------------------
# ToolSchema — tool definition forwarded to the LLM
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSchema:
    """A single tool definition forwarded to the LLM API.

    ``cache=True`` requests prompt-caching on this tool schema block
    (Anthropic only; others ignore it).
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    cache: bool = False


# ---------------------------------------------------------------------------
# ModelInfo — advertised model metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelInfo:
    """Metadata about a named model, returned by LLMManager.models()."""

    id: str
    """User-defined logical name (e.g. ``"claude-opus"``)."""

    provider_type: str
    """Provider type (e.g. ``"anthropic"``)."""

    model_id: str
    """Actual API model identifier (e.g. ``"claude-opus-4-6"``)."""

    context_window: int | None = None
    """Max context window in tokens, if known."""
