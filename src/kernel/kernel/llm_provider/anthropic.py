"""AnthropicProvider — Anthropic Messages API backend."""

from __future__ import annotations

import orjson
import logging
from collections.abc import AsyncGenerator

from anthropic import AsyncAnthropic
from anthropic._types import NOT_GIVEN

from kernel.llm.types import (
    LLMChunk,
    Message,
    ModelInfo,
    PromptSection,
    StreamError,
    TextChunk,
    ThoughtChunk,
    ToolSchema,
    ToolUseChunk,
    UsageChunk,
)
from kernel.llm_provider.base import Provider
from kernel.llm_provider.errors import MediaSizeError, PromptTooLongError, ProviderError
from kernel.llm_provider.format.anthropic import (
    messages_to_anthropic,
    schemas_to_anthropic,
    sections_to_anthropic,
)

logger = logging.getLogger(__name__)

# Hard-coded context windows (tokens) — Anthropic has no list-models API.
_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-opus-4-5": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "claude-3-opus-20240229": 200_000,
}

_THINKING_BUDGET = 8_000


class AnthropicProvider(Provider):
    """Anthropic Messages API backend.

    One instance per unique ``(api_key, base_url)`` combination.
    ``prompt_caching`` and ``thinking`` are passed per-call (not stored
    here) because multiple model entries sharing this instance may have
    different settings.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None,
    ) -> None:
        self._client = AsyncAnthropic(
            api_key=api_key or None,
            base_url=base_url or None,
        )

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
        return self._stream(
            system=system,
            messages=messages,
            tool_schemas=tool_schemas,
            model_id=model_id,
            temperature=temperature,
            thinking=thinking,
            max_tokens=max_tokens,
            prompt_caching=prompt_caching,
        )

    async def _stream(
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
        api_system = sections_to_anthropic(system, prompt_caching=prompt_caching)
        api_messages = messages_to_anthropic(messages)
        api_tools = schemas_to_anthropic(tool_schemas, prompt_caching=prompt_caching)

        # Extended thinking requires temperature to be unset.
        thinking_param = (
            {"type": "enabled", "budget_tokens": _THINKING_BUDGET} if thinking else NOT_GIVEN
        )
        temperature_param = (
            NOT_GIVEN if thinking else (temperature if temperature is not None else NOT_GIVEN)
        )

        try:
            async with self._client.messages.stream(
                model=model_id,
                system=api_system or NOT_GIVEN,  # type: ignore[arg-type]
                messages=api_messages,  # type: ignore[arg-type]
                tools=api_tools or NOT_GIVEN,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                temperature=temperature_param,  # type: ignore[arg-type]
                thinking=thinking_param,  # type: ignore[arg-type]
            ) as stream:
                # index → {id, name, input_json}
                tool_buffers: dict[int, dict[str, str]] = {}

                async for event in stream:
                    match event.type:
                        case "content_block_start":
                            if event.content_block.type == "tool_use":
                                tool_buffers[event.index] = {
                                    "id": event.content_block.id,
                                    "name": event.content_block.name,
                                    "input_json": "",
                                }
                        case "content_block_delta":
                            delta = event.delta
                            match delta.type:
                                case "text_delta":
                                    yield TextChunk(content=delta.text)
                                case "thinking_delta":
                                    yield ThoughtChunk(content=delta.thinking)
                                case "signature_delta":
                                    yield ThoughtChunk(content="", signature=delta.signature)
                                case "input_json_delta":
                                    if event.index in tool_buffers:
                                        tool_buffers[event.index]["input_json"] += (
                                            delta.partial_json
                                        )
                        case "content_block_stop":
                            if event.index in tool_buffers:
                                buf = tool_buffers.pop(event.index)
                                try:
                                    parsed = orjson.loads(buf["input_json"] or "{}")
                                except orjson.JSONDecodeError:
                                    logger.warning(
                                        "Failed to parse tool input JSON for '%s'", buf["name"]
                                    )
                                    parsed = {}
                                yield ToolUseChunk(id=buf["id"], name=buf["name"], input=parsed)

                msg = await stream.get_final_message()
                u = msg.usage
                yield UsageChunk(
                    input_tokens=u.input_tokens,
                    output_tokens=u.output_tokens,
                    cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                    cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
                    stop_reason=msg.stop_reason,
                )

        except (ProviderError, PromptTooLongError, MediaSizeError):
            raise
        except Exception as exc:
            msg_lower = str(exc).lower()
            if "too long" in msg_lower or ("context" in msg_lower and "length" in msg_lower):
                raise PromptTooLongError(str(exc)) from exc
            if any(k in msg_lower for k in ("authentication", "api key", "invalid x-api-key")):
                raise ProviderError(f"Anthropic auth error: {exc}") from exc
            if any(k in msg_lower for k in ("image", "media")) and any(
                k in msg_lower
                for k in ("too large", "size", "limit", "exceed", "could not process")
            ):
                raise MediaSizeError(str(exc)) from exc
            logger.warning("AnthropicProvider stream error: %s", exc)
            yield StreamError(message=str(exc))

    async def models(self) -> list[ModelInfo]:
        # Anthropic has no reliable list-models endpoint with context window
        # info, so we return an empty list here. LLMManager builds ModelInfo
        # from the user's config instead.
        return []

    async def discover_models(self) -> list[str]:
        """Query ``GET /v1/models`` via the Anthropic SDK."""
        try:
            result = await self._client.models.list(limit=100)
            return [m.id for m in result.data]
        except Exception:
            logger.warning("AnthropicProvider: model discovery failed", exc_info=True)
            return []

    async def context_window(self, model_id: str) -> int | None:
        return _CONTEXT_WINDOWS.get(model_id)
