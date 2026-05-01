"""Provider ABC and universal message types.

The engine ONLY works with these types — no provider-specific formats leak out.
Each provider translates between these types and its native API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal

from pydantic import BaseModel

from daemon.engine.stream import StreamEvent, ToolDefinition

if TYPE_CHECKING:
    from daemon.engine.context import PromptSection


class TextContent(BaseModel):
    """Plain text content block within a message."""

    type: Literal["text"] = "text"
    text: str


class ImageContent(BaseModel):
    """Image content block (user attachments or tool-returned images).

    Mustang stores the raw base64 on disk (``~/.mustang/cache/images/``)
    and JSONL persistence keeps only ``source_sha256 + media_type``; the
    in-memory model eagerly carries ``data_base64`` until serialised.

    Attributes:
        media_type: MIME type accepted by multimodal LLMs
            (``image/png``/``image/jpeg``/``image/webp``/``image/gif``).
        data_base64: Base64-encoded image bytes.  Always populated at
            construction; emptied by the cache-store step before a
            JSONL entry is written.
        source_sha256: Hex SHA-256 of the raw bytes, used as the cache
            key.  ``None`` until cached (lets callers construct inline
            first, hash-and-store later).
        source_path: Original filesystem path, kept for CLI display /
            debugging.  Not sent to providers.
    """

    type: Literal["image"] = "image"
    media_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
    data_base64: str
    source_sha256: str | None = None
    source_path: str | None = None


class ToolUseContent(BaseModel):
    """LLM's request to invoke a tool."""

    type: Literal["tool_use"] = "tool_use"
    tool_call_id: str
    name: str
    arguments: dict[str, Any]


class ToolResultContent(BaseModel):
    """Result of a tool execution, returned to the LLM.

    Attributes:
        output: Primary textual result (always present).
        is_error: Whether the tool reported an error.
        image_parts: Optional image blocks produced by the tool.
            Providers that support multimodal tool results (Anthropic)
            fold these into the native ``content`` array; others
            silently drop them and prepend a text warning.
    """

    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    output: str
    is_error: bool = False
    image_parts: list[ImageContent] | None = None


MessageContent = TextContent | ImageContent | ToolUseContent | ToolResultContent


class Message(BaseModel):
    """Universal message — the engine's lingua franca.

    Use the factory classmethods for convenient construction.
    """

    role: Literal["user", "assistant", "tool"]
    content: list[MessageContent]

    @classmethod
    def user(cls, text: str, images: list[ImageContent] | None = None) -> Message:
        """Create a user message, optionally with image attachments."""
        content: list[MessageContent] = [TextContent(text=text)]
        if images:
            content.extend(images)
        return cls(role="user", content=content)

    @classmethod
    def assistant_text(cls, text: str) -> Message:
        """Create an assistant text message."""
        return cls(role="assistant", content=[TextContent(text=text)])

    @classmethod
    def assistant_tool_use(cls, tool_call_id: str, name: str, arguments: dict[str, Any]) -> Message:
        """Create an assistant message requesting a tool call."""
        return cls(
            role="assistant",
            content=[ToolUseContent(tool_call_id=tool_call_id, name=name, arguments=arguments)],
        )

    @classmethod
    def tool_result(
        cls,
        tool_call_id: str,
        output: str,
        is_error: bool = False,
        image_parts: list[ImageContent] | None = None,
    ) -> Message:
        """Create a tool-result message to feed back to the LLM."""
        return cls(
            role="tool",
            content=[
                ToolResultContent(
                    tool_call_id=tool_call_id,
                    output=output,
                    is_error=is_error,
                    image_parts=image_parts,
                )
            ],
        )


class ModelIdentity(BaseModel):
    """Provider-specific model identity metadata.

    Returned by :meth:`Provider.model_identity` and used to enrich
    the system prompt's environment section with knowledge cutoff,
    model family info, and provider-specific notes.
    """

    knowledge_cutoff: str | None = None
    """Training data cutoff (e.g. ``"Early 2025"``)."""
    identity_lines: list[str] | None = None
    """Extra lines to append to the environment section.

    Each string becomes a `` - ...`` bullet in the environment block.
    Use for model family info, latest model IDs, capability notes, etc.
    """


class ModelInfo(BaseModel):
    """Metadata about a model available from a provider."""

    id: str
    name: str
    provider: str
    supports_tools: bool = True
    knowledge_cutoff: str | None = None
    """Training data cutoff (e.g. ``"Early 2025"``).  ``None`` = unknown."""
    identity_prompt: str | None = None
    """Provider-specific model identity text injected into the environment
    section of the system prompt.  Includes model family info, latest
    model IDs, and provider-specific notes.  ``None`` omits the block."""


class Provider(ABC):
    """Abstract base for LLM providers.

    Implementers translate in both directions:
      - IN:  universal Message/ToolDefinition → provider's native API format
      - OUT: provider's native API response → universal StreamEvent
    """

    name: str

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: list[PromptSection] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a completion from the LLM provider.

        Args:
            messages: Conversation history in universal format.
            tools: Tool definitions for function calling.
            model: Model ID (uses provider default if None).
            system: Structured system prompt sections.  Each section
                carries a ``cacheable`` flag — providers that support
                per-block caching (Anthropic) use it; others call
                :func:`prompt_sections_to_text` to join into a string.
            **kwargs: Provider-specific options (temperature, etc.).

        Yields:
            TextDelta, ToolCallStart, StreamEnd, or StreamError events.

        Raises:
            ProviderError: On unexpected (non-API) errors.
        """
        ...

    def model_identity(self, model_id: str | None = None) -> ModelIdentity | None:
        """Return provider-specific model identity metadata.

        Subclasses override this to supply knowledge cutoff, model
        family information, and any provider-specific notes that
        should appear in the system prompt's environment section.

        The default implementation returns ``None`` (no identity info).

        Args:
            model_id: The active model ID.  Providers use this to look
                up the correct metadata from their internal tables.
        """
        return None

    async def query_context_window(self) -> int | None:
        """Query the model's context window size from the provider API.

        Default implementation returns ``None`` (not available).
        Subclasses may override to query ``/v1/models`` or similar.

        Returns:
            Context window in tokens, or ``None`` if unavailable.
        """
        return None

    @abstractmethod
    async def models(self) -> list[ModelInfo]:
        """List models available from this provider."""
        ...


# ----- Resolve forward refs ------------------------------------------------
#
# ``daemon.engine.stream.ToolCallResult`` carries an
# ``image_parts: list["ImageContent"] | None`` field, but its module
# can't import ``ImageContent`` at module load time (would create a
# circular import — ``providers.base`` imports from ``stream``).  Now
# that ``ImageContent`` is defined, rebuild the dependent model so the
# string forward-ref resolves at validation time.
from daemon.engine.stream import ToolCallResult as _ToolCallResult

_ToolCallResult.model_rebuild(_types_namespace={"ImageContent": ImageContent})
del _ToolCallResult
