"""Anthropic provider — streaming Messages API with extended thinking.

Parallel to :class:`OpenAIBaseProvider`; does *not* inherit from it
because the wire format is fundamentally different (content blocks
vs OpenAI chat messages).  Shares the universal
:class:`StreamEvent` emission — the orchestrator sees Anthropic
turns the same as any other provider.

Extended thinking support is driven by the
``provider.<name>.thinking`` config field (see
:mod:`daemon.providers.thinking_config`).  The Anthropic SDK's
``thinking_delta`` streaming events are re-emitted as Mustang's
universal :class:`ThinkingDelta` events so the CLI renderer is
provider-agnostic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

from anthropic import APIError, AsyncAnthropic

from daemon.config.schema import ProviderRuntimeConfig
from daemon.engine.stream import (
    StreamEnd,
    StreamError,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolCallStart,
    UsageInfo,
)
from daemon.errors import PromptTooLongError, ProviderError
from daemon.providers.anthropic_format import (
    messages_to_anthropic,
    system_to_anthropic,
    tools_to_anthropic,
)
from daemon.providers.base import Message, ModelIdentity, ModelInfo, Provider, ToolDefinition
from daemon.providers.thinking_config import to_anthropic_param

if TYPE_CHECKING:
    from daemon.engine.context import PromptSection

logger = logging.getLogger(__name__)


# Fallback context-window table — used when the model does not appear
# in ``/v1/models``.  Anthropic publishes these in their docs.
_KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    # Claude 4 family
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku-4": 200_000,
    # Claude 3.5 family
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    # Claude 3 family
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
}


def _lookup_context_window(model: str) -> int | None:
    """Best-effort context window based on the model id prefix."""
    for prefix, cw in _KNOWN_CONTEXT_WINDOWS.items():
        if model.startswith(prefix):
            return cw
    return None


# Model prefix → knowledge cutoff.
_ANTHROPIC_CUTOFFS: dict[str, str] = {
    "claude-opus-4": "Early 2025",
    "claude-sonnet-4": "Early 2025",
    "claude-haiku-4": "Early 2025",
    "claude-3-5-sonnet": "Early 2024",
    "claude-3-5-haiku": "Early 2024",
    "claude-3-opus": "Early 2024",
    "claude-3-sonnet": "November 2023",
    "claude-3-haiku": "August 2023",
}

# Shared identity lines for all Claude models.
_ANTHROPIC_IDENTITY_LINES: list[str] = [
    (
        "The most recent Claude model family is Claude 4. "
        "Model IDs — Opus 4: 'claude-opus-4-20250514', "
        "Sonnet 4: 'claude-sonnet-4-20250514', "
        "Haiku 4: 'claude-haiku-4-20250514'. "
        "When building AI applications, default to the latest and "
        "most capable Claude models."
    ),
]


def _lookup_cutoff(model: str) -> str | None:
    for prefix, cutoff in _ANTHROPIC_CUTOFFS.items():
        if model.startswith(prefix):
            return cutoff
    return None


# ---------------------------------------------------------------------------
# Context overflow detection
# ---------------------------------------------------------------------------


def _is_anthropic_context_overflow(exc: APIError) -> bool:
    """Return ``True`` if *exc* signals a context-window overflow."""
    msg = str(exc).lower()
    return "prompt is too long" in msg or "exceeds the maximum" in msg


class AnthropicProvider(Provider):
    """Provider for Anthropic's Messages API.

    Args:
        config: Resolved provider config (api_key, model, etc.).
        provider_name: Logical name used in the registry
            (e.g. ``"claude"``).
    """

    name = "anthropic"

    def __init__(self, config: ProviderRuntimeConfig, provider_name: str = "anthropic") -> None:
        self.name = provider_name
        self._config = config
        self._default_model = config.model
        client_kwargs: dict[str, Any] = {"api_key": config.api_key or None}
        # Anthropic's SDK accepts base_url=None gracefully; pass only
        # when the user explicitly set a non-default.
        if config.base_url and config.base_url != "https://api.anthropic.com":
            client_kwargs["base_url"] = config.base_url
        self._client = AsyncAnthropic(**client_kwargs)

    # ------------------------------------------------------------------
    # Model identity
    # ------------------------------------------------------------------

    def model_identity(self, model_id: str | None = None) -> ModelIdentity | None:
        """Return Anthropic-specific model identity metadata."""
        mid = model_id or self._default_model
        cutoff = _lookup_cutoff(mid) if mid else None
        return ModelIdentity(
            knowledge_cutoff=cutoff,
            identity_lines=_ANTHROPIC_IDENTITY_LINES,
        )

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
        """Stream a response from Anthropic, yielding universal events.

        Accumulates ``input_json_delta`` chunks per tool-use block;
        emits a single :class:`ToolCallStart` once the block closes.
        """
        model = model or self._default_model
        prompt_caching = self._config.prompt_caching is not False

        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages_to_anthropic(messages),
            # Anthropic requires ``max_tokens`` — pick a large but
            # sensible default if the caller didn't pass one.
            "max_tokens": kwargs.pop("max_tokens", 8192),
        }

        if system:
            create_kwargs["system"] = system_to_anthropic(
                system,
                prompt_caching=prompt_caching,
            )
        if tools:
            create_kwargs["tools"] = tools_to_anthropic(
                tools,
                cache_tools=prompt_caching,
            )

        thinking_param = to_anthropic_param(self._config.thinking)
        if thinking_param is not None:
            create_kwargs["thinking"] = thinking_param

        # Pass through anything else the caller provided (temperature,
        # top_p, …).  The SDK ignores unknown kwargs with a warning.
        create_kwargs.update(kwargs)

        # Per-tool-block accumulation state.
        tool_blocks: dict[int, dict[str, Any]] = {}
        usage_input = 0
        usage_output = 0
        cache_creation = 0
        cache_read = 0

        try:
            async with self._client.messages.stream(**create_kwargs) as stream:
                async for event in stream:
                    etype = getattr(event, "type", None)

                    idx = getattr(event, "index", None)

                    if etype == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if block is None or idx is None:
                            continue
                        btype = getattr(block, "type", None)
                        if btype == "tool_use":
                            tool_blocks[idx] = {
                                "id": getattr(block, "id", ""),
                                "name": getattr(block, "name", ""),
                                "arguments": "",
                            }

                    elif etype == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta is None:
                            continue
                        dtype = getattr(delta, "type", None)
                        if dtype == "text_delta":
                            text = getattr(delta, "text", "")
                            if text:
                                yield TextDelta(content=text)
                        elif dtype == "thinking_delta":
                            thinking = getattr(delta, "thinking", "")
                            if thinking:
                                yield ThinkingDelta(content=thinking)
                        elif dtype == "input_json_delta" and idx is not None:
                            pj = getattr(delta, "partial_json", "")
                            entry = tool_blocks.get(idx)
                            if entry is not None and pj:
                                entry["arguments"] += pj

                    elif etype == "content_block_stop" and idx is not None:
                        entry = tool_blocks.pop(idx, None)
                        if entry is None:
                            continue
                        args = _parse_tool_args(entry["arguments"])
                        yield ToolCallStart(
                            tool_call_id=entry["id"],
                            tool_name=entry["name"],
                            arguments=args,
                        )

                    elif etype == "message_delta":
                        # Usage arrives incrementally on message_delta.
                        usage = getattr(event, "usage", None)
                        if usage is not None:
                            usage_output = getattr(usage, "output_tokens", usage_output)

                    elif etype == "message_start":
                        msg = getattr(event, "message", None)
                        if msg is not None:
                            usage = getattr(msg, "usage", None)
                            if usage is not None:
                                usage_input = getattr(usage, "input_tokens", usage_input)
                                cache_creation = (
                                    getattr(
                                        usage,
                                        "cache_creation_input_tokens",
                                        0,
                                    )
                                    or 0
                                )
                                cache_read = (
                                    getattr(
                                        usage,
                                        "cache_read_input_tokens",
                                        0,
                                    )
                                    or 0
                                )

        except APIError as exc:
            if _is_anthropic_context_overflow(exc):
                raise PromptTooLongError(str(exc)) from exc
            logger.error("Anthropic API error: %s", exc)
            yield StreamError(message=str(exc))
            return
        except Exception as exc:
            raise ProviderError(f"Unexpected error calling {self.name}: {exc}") from exc

        yield StreamEnd(
            usage=UsageInfo(
                input_tokens=usage_input,
                output_tokens=usage_output,
                cache_creation_tokens=cache_creation,
                cache_read_tokens=cache_read,
            )
        )

    # ------------------------------------------------------------------
    # Model info
    # ------------------------------------------------------------------

    async def query_context_window(self) -> int | None:
        """Return the context window for the configured model.

        Tries the Anthropic ``/v1/models/{id}`` endpoint first, then
        falls back to a built-in table so offline / key-less test
        runs still return sensible numbers.
        """
        try:
            info = await self._client.models.retrieve(self._default_model)
            cw = getattr(info, "context_window", None)
            if isinstance(cw, int) and cw > 0:
                return cw
        except Exception:
            logger.debug("Could not fetch model info from Anthropic API")
        return _lookup_context_window(self._default_model)

    async def models(self) -> list[ModelInfo]:
        """List available Anthropic models; falls back to the default."""
        try:
            listing = await self._client.models.list()
            return [
                ModelInfo(
                    id=m.id,
                    name=getattr(m, "display_name", m.id),
                    provider=self.name,
                    supports_tools=True,
                )
                for m in listing.data
            ]
        except Exception as exc:
            logger.warning("Failed to list Anthropic models: %s", exc)
            return [
                ModelInfo(
                    id=self._default_model,
                    name=self._default_model,
                    provider=self.name,
                    supports_tools=True,
                )
            ]


def _parse_tool_args(raw: str) -> dict[str, Any]:
    """Parse a partial-JSON accumulation into a dict; empty → ``{}``."""
    import json

    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    except json.JSONDecodeError:
        return {"_raw": raw}


__all__ = ["AnthropicProvider"]
