"""Provider ABC — internal abstraction for a single LLM backend endpoint.

``Provider`` instances are managed by ``LLMProviderManager``, which
deduplicates them by ``(type, api_key, base_url)``.  Multiple model
entries with the same credentials share one Provider instance.

This class is an internal implementation detail of ``kernel.llm_provider``.
External code interacts with the LLM layer only through ``LLMManager``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

from kernel.llm.types import (
    LLMChunk,
    Message,
    ModelInfo,
    PromptSection,
    ToolSchema,
)


class Provider(ABC):
    """Single LLM endpoint communication implementation.

    Each instance corresponds to one unique ``(provider_type, api_key,
    base_url)`` combination.  It holds the SDK client and knows how to
    translate universal types to the SDK's native request format and
    back.

    It does NOT know the logical model names ("claude-opus") — it only
    receives ``model_id`` (the actual API identifier).
    ``prompt_caching`` and ``thinking`` are passed per-call since
    multiple models sharing one Provider instance may have different
    settings for these.
    """

    @abstractmethod
    def stream(
        self,
        *,
        system: list[PromptSection],
        messages: list[Message],
        tool_schemas: list[ToolSchema],
        model_id: str,
        temperature: float | None,
        thinking: bool,
        max_tokens: int,
        prompt_caching: bool,
    ) -> AsyncGenerator[LLMChunk, None]:
        """Stream a completion request.

        Translates universal types to SDK-native format, drives the
        streaming API call, and yields ``LLMChunk`` values.

        Contract:
        - ``ToolUseChunk`` is emitted once, after all input JSON is
          received and parsed (at ``content_block_stop``).
        - ``UsageChunk`` is emitted exactly once at stream end.
        - Transient errors → ``StreamError`` chunk (not raised).
        - Auth / config failures → raise ``ProviderError``.
        """
        ...

    @abstractmethod
    async def models(self) -> list[ModelInfo]:
        """Return metadata for models served by this provider instance."""
        ...

    async def discover_models(self) -> list[str]:
        """Query the provider API and return available model IDs.

        Used during ``provider_add`` to auto-populate the ``models``
        list when the user omits it.  Providers that don't support
        model discovery (e.g. Bedrock) return an empty list — the
        caller should require the user to specify models manually.
        """
        return []

    async def context_window(self, model_id: str) -> int | None:
        """Return context window size in tokens for ``model_id``.

        Default returns ``None``.  Subclasses override via built-in table
        or API query.  Used by the Orchestrator's Compactor.
        """
        return None

    async def aclose(self) -> None:
        """Close underlying connections (e.g. httpx client).

        Called by ``LLMProviderManager.shutdown()``.  Default is a no-op;
        providers that hold open connections override this.
        """
