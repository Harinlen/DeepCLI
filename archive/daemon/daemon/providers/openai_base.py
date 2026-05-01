"""OpenAI-compatible base provider — shared message translation and streaming.

Provides the common logic used by all providers that speak the OpenAI
chat/completions API (MiniMax, DeepSeek, Ollama, llama.cpp, vLLM, etc.).

Subclasses override provider-specific behaviour such as
``query_context_window()`` or ``models()`` while inheriting the full
``stream()`` implementation and message format translation.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

from openai import AsyncOpenAI, OpenAIError

from daemon.config.schema import ProviderRuntimeConfig
from daemon.errors import PromptTooLongError, ProviderError
from daemon.engine.context import prompt_sections_to_text
from daemon.engine.stream import (
    StreamEnd,
    StreamError,
    StreamEvent,
    ThinkingDelta,
    ToolCallStart,
    UsageInfo,
)
from daemon.providers.base import (
    Message,
    ModelIdentity,
    ModelInfo,
    Provider,
    ToolDefinition,
)
from daemon.providers.openai_format import messages_to_openai, tools_to_openai
from daemon.providers.think_tag_parser import (
    _ThinkTagParser,
    _split_at_partial as _split_at_partial,
)

if TYPE_CHECKING:
    from daemon.engine.context import PromptSection

logger = logging.getLogger(__name__)

# OpenAI model prefix → knowledge cutoff.
_OPENAI_CUTOFFS: dict[str, str] = {
    "gpt-4.1": "June 2024",
    "gpt-4o": "October 2023",
    "gpt-4-turbo": "December 2023",
    "o3": "June 2024",
    "o4-mini": "June 2024",
}

_OPENAI_IDENTITY_LINES: list[str] = [
    (
        "The most recent OpenAI model family is GPT-4.1 and o-series. "
        "Model IDs — gpt-4.1, gpt-4.1-mini, gpt-4.1-nano, o3, o4-mini. "
        "When building AI applications, default to the latest models."
    ),
]


def _openai_identity(model: str) -> ModelIdentity | None:
    """Return OpenAI identity if *model* matches a known OpenAI prefix."""
    for prefix in _OPENAI_CUTOFFS:
        if model.startswith(prefix):
            return ModelIdentity(
                knowledge_cutoff=_OPENAI_CUTOFFS[prefix],
                identity_lines=_OPENAI_IDENTITY_LINES,
            )
    # Also match o-series models (o3, o4-mini)
    if model.startswith("o") and model[1:2].isdigit():
        for prefix, cutoff in _OPENAI_CUTOFFS.items():
            if model.startswith(prefix):
                return ModelIdentity(
                    knowledge_cutoff=cutoff,
                    identity_lines=_OPENAI_IDENTITY_LINES,
                )
    return None


# ---------------------------------------------------------------------------
# Context overflow detection
# ---------------------------------------------------------------------------

# Patterns that indicate the prompt exceeds the model's context window.
_CONTEXT_OVERFLOW_PATTERNS = (
    "maximum context length",
    "context_length_exceeded",
    "too many tokens",
    "reduce the length",
    "prompt is too long",
)


def _is_context_overflow(exc: OpenAIError) -> bool:
    """Return ``True`` if *exc* signals a context-window overflow."""
    msg = str(exc).lower()
    return any(p in msg for p in _CONTEXT_OVERFLOW_PATTERNS)


class OpenAIBaseProvider(Provider):
    """Base provider for any endpoint that speaks the OpenAI chat API.

    Provides the full ``stream()`` implementation plus message/tool
    format translation.  Subclasses override ``query_context_window()``
    and ``models()`` for provider-specific behaviour.

    Args:
        config: Resolved provider config.
        provider_name: Logical name used in registry (e.g. ``"minimax"``).
    """

    name = "openai_base"

    def __init__(self, config: ProviderRuntimeConfig, provider_name: str = "local") -> None:
        self.name = provider_name
        self._config = config
        self._client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
        )
        self._default_model = config.model

    # ------------------------------------------------------------------
    # Model identity
    # ------------------------------------------------------------------

    def model_identity(self, model_id: str | None = None) -> ModelIdentity | None:
        """Return identity if the model matches a known OpenAI model."""
        mid = model_id or self._default_model
        return _openai_identity(mid) if mid else None

    # ------------------------------------------------------------------
    # Message format translation (universal ↔ OpenAI)
    # ------------------------------------------------------------------

    # Translators live in providers.openai_format — kept as staticmethod
    # aliases here so subclasses and tests that reach for the old names
    # continue to resolve.
    _to_openai_messages = staticmethod(messages_to_openai)
    _to_openai_tools = staticmethod(tools_to_openai)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: list[PromptSection] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion, yielding universal StreamEvents.

        Tool calls are accumulated across chunks before being emitted
        as ToolCallStart events on finish_reason.

        Raises:
            ProviderError: On unexpected (non-API) errors.
        """
        model = model or self._default_model
        system_text = prompt_sections_to_text(system) if system else None
        oai_messages = self._to_openai_messages(messages, system_text)

        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": oai_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if tools:
            create_kwargs["tools"] = self._to_openai_tools(tools)

        # Merge any extra kwargs (temperature, max_tokens, etc.)
        create_kwargs.update(kwargs)

        try:
            response = await self._client.chat.completions.create(**create_kwargs)
        except OpenAIError as e:
            if _is_context_overflow(e):
                raise PromptTooLongError(str(e)) from e
            logger.error("OpenAI API error: %s", e)
            yield StreamError(message=str(e))
            return
        except Exception as e:
            raise ProviderError(f"Unexpected error calling {self.name}: {e}") from e

        # Accumulate tool calls across chunks (streamed in pieces)
        pending_tool_calls: dict[int, dict[str, Any]] = {}
        usage = UsageInfo()
        think_parser = _ThinkTagParser()

        try:
            async for chunk in response:
                # Usage info (final chunk)
                if chunk.usage:
                    usage = UsageInfo(
                        input_tokens=chunk.usage.prompt_tokens or 0,
                        output_tokens=chunk.usage.completion_tokens or 0,
                    )

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # Thinking / reasoning content (e.g. Qwen3.5)
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield ThinkingDelta(content=reasoning)

                # Text content — parse <think> tags to separate
                # thinking from visible output (e.g. MiniMax).
                if delta.content:
                    for event in think_parser.feed(delta.content):
                        yield event

                # Tool calls (streamed incrementally)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx is None:
                            logger.warning("Skipping tool_call chunk with index=None")
                            continue
                        if idx not in pending_tool_calls:
                            pending_tool_calls[idx] = {
                                "id": tc.id or "",
                                "name": "",
                                "arguments": "",
                            }

                        entry = pending_tool_calls[idx]
                        if tc.id:
                            entry["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                entry["name"] = tc.function.name
                            if tc.function.arguments:
                                entry["arguments"] += tc.function.arguments

                # Finish reason — emit accumulated tool calls
                finish = chunk.choices[0].finish_reason
                if finish == "tool_calls" or (finish == "stop" and pending_tool_calls):
                    for _idx in sorted(pending_tool_calls):
                        entry = pending_tool_calls[_idx]
                        try:
                            args = json.loads(entry["arguments"]) if entry["arguments"] else {}
                        except json.JSONDecodeError:
                            args = {"_raw": entry["arguments"]}
                        yield ToolCallStart(
                            tool_call_id=entry["id"],
                            tool_name=entry["name"],
                            arguments=args,
                        )
                    pending_tool_calls.clear()

        except OpenAIError as e:
            logger.error("Stream error: %s", e)
            yield StreamError(message=str(e))
            return
        except Exception as e:
            raise ProviderError(f"Unexpected stream error from {self.name}: {e}") from e

        yield StreamEnd(usage=usage)

    # ------------------------------------------------------------------
    # Model info (default implementations — subclasses may override)
    # ------------------------------------------------------------------

    async def query_context_window(self) -> int | None:
        """Query context window from the ``/v1/models`` endpoint.

        Some OpenAI-compatible servers (e.g. Ollama) include a
        ``context_length`` or ``context_window`` field in the model
        metadata.  Returns ``None`` if the endpoint does not provide
        this information.

        Subclasses should override this if the provider has a different
        mechanism for reporting context window size.
        """
        try:
            result = await self._client.models.list()
            for m in result.data:
                if m.id == self._default_model:
                    # Different servers use different field names.
                    for attr in ("context_length", "context_window"):
                        val = getattr(m, attr, None)
                        if isinstance(val, int) and val > 0:
                            return val
        except Exception:
            logger.debug("Could not query context window from %s", self._config.base_url)
        return None

    async def models(self) -> list[ModelInfo]:
        """List models available at this endpoint. Falls back to default on error."""
        try:
            result = await self._client.models.list()
            return [
                ModelInfo(
                    id=m.id,
                    name=m.id,
                    provider=self.name,
                    supports_tools=True,
                )
                for m in result.data
            ]
        except Exception as e:
            logger.warning("Failed to list models from %s: %s", self._config.base_url, e)
            # Return at least the configured default model
            return [
                ModelInfo(
                    id=self._default_model,
                    name=self._default_model,
                    provider=self.name,
                    supports_tools=True,
                )
            ]
