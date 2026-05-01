"""OpenAICompatibleProvider — OpenAI Chat Completions API via httpx SSE.

Uses ``httpx.AsyncClient`` directly — no openai SDK dependency.
"""

from __future__ import annotations

import orjson
import logging
from collections.abc import AsyncGenerator

import httpx

from kernel.llm.types import (
    LLMChunk,
    Message,
    ModelInfo,
    PromptSection,
    StreamError,
    TextChunk,
    ToolSchema,
    ToolUseChunk,
    UsageChunk,
)
from kernel.llm_provider.base import Provider
from kernel.llm_provider.errors import PromptTooLongError, ProviderError
from kernel.llm_provider.format.openai import messages_to_openai, schemas_to_openai

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatibleProvider(Provider):
    """OpenAI Chat Completions-compatible backend via httpx SSE.

    One instance per unique ``(base_url, api_key)`` combination.
    ``prompt_caching`` and ``thinking`` have no effect on this provider
    (passed through for API compatibility but ignored).

    If the upstream endpoint returns ``prompt_tokens_details.cached_tokens``
    (OpenAI, or compatible backends), the value is forwarded in
    ``UsageChunk.cache_read_tokens``.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None,
    ) -> None:
        base = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._chat_url = f"{base}/chat/completions"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0),
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
            max_tokens=max_tokens,
        )

    async def _stream(
        self,
        *,
        system: list[PromptSection],
        messages: list[Message],
        tool_schemas: list[ToolSchema],
        model_id: str,
        temperature: float | None,
        max_tokens: int,
    ) -> AsyncGenerator[LLMChunk, None]:
        body: dict = {
            "model": model_id,
            "messages": messages_to_openai(messages, system),
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if temperature is not None:
            body["temperature"] = temperature
        if tool_schemas:
            body["tools"] = schemas_to_openai(tool_schemas)
            body["tool_choice"] = "auto"

        try:
            async with self._client.stream("POST", self._chat_url, json=body) as resp:
                if resp.status_code == 401:
                    raise ProviderError(
                        f"OpenAI-compatible auth error (HTTP 401): {self._chat_url}"
                    )
                if resp.status_code == 413:
                    raise PromptTooLongError(f"Prompt too long (HTTP 413): {self._chat_url}")
                if resp.status_code >= 400:
                    raw = await resp.aread()
                    raise ProviderError(
                        f"HTTP {resp.status_code} from {self._chat_url}: "
                        f"{raw.decode(errors='replace')[:200]}"
                    )

                # index → {id, name, arguments}
                tool_buffers: dict[int, dict[str, str]] = {}
                input_tokens = 0
                output_tokens = 0
                cache_read_tokens = 0

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[len("data: ") :]
                    if payload.strip() == "[DONE]":
                        break

                    try:
                        chunk = orjson.loads(payload)
                    except orjson.JSONDecodeError:
                        continue

                    usage = chunk.get("usage")
                    if usage:
                        input_tokens = usage.get("prompt_tokens", 0)
                        output_tokens = usage.get("completion_tokens", 0)
                        details = usage.get("prompt_tokens_details") or {}
                        cache_read_tokens = details.get("cached_tokens", 0)

                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        finish_reason = choice.get("finish_reason")

                        text = delta.get("content")
                        if text:
                            yield TextChunk(content=text)

                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            fn = tc.get("function") or {}
                            if tc_id := tc.get("id"):
                                tool_buffers[idx] = {
                                    "id": tc_id,
                                    "name": fn.get("name", ""),
                                    "arguments": fn.get("arguments", ""),
                                }
                            elif idx in tool_buffers:
                                if fn.get("name"):
                                    tool_buffers[idx]["name"] = fn["name"]
                                tool_buffers[idx]["arguments"] += fn.get("arguments", "")

                        if finish_reason == "tool_calls":
                            for buf in tool_buffers.values():
                                try:
                                    parsed = orjson.loads(buf["arguments"] or "{}")
                                except orjson.JSONDecodeError:
                                    parsed = {}
                                yield ToolUseChunk(id=buf["id"], name=buf["name"], input=parsed)
                            tool_buffers.clear()

                # Flush any unflushed tool calls
                for buf in tool_buffers.values():
                    try:
                        parsed = orjson.loads(buf["arguments"] or "{}")
                    except orjson.JSONDecodeError:
                        parsed = {}
                    yield ToolUseChunk(id=buf["id"], name=buf["name"], input=parsed)

                yield UsageChunk(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                )

        except (ProviderError, PromptTooLongError):
            raise
        except httpx.TimeoutException as exc:
            logger.warning("OpenAICompatibleProvider timeout: %s", exc)
            yield StreamError(message=f"Request timed out: {exc}")
        except httpx.HTTPError as exc:
            logger.warning("OpenAICompatibleProvider HTTP error: %s", exc)
            yield StreamError(message=str(exc))
        except Exception as exc:
            logger.warning("OpenAICompatibleProvider unexpected error: %s", exc)
            yield StreamError(message=str(exc))

    async def models(self) -> list[ModelInfo]:
        return []

    async def discover_models(self) -> list[str]:
        """Query ``GET /v1/models`` (standard OpenAI-compatible endpoint)."""
        try:
            models_url = self._chat_url.replace("/chat/completions", "/models")
            resp = await self._client.get(models_url)
            if resp.status_code != 200:
                logger.warning(
                    "OpenAICompatibleProvider: model discovery got HTTP %d",
                    resp.status_code,
                )
                return []
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
        except Exception:
            logger.warning("OpenAICompatibleProvider: model discovery failed", exc_info=True)
            return []

    async def aclose(self) -> None:
        await self._client.aclose()
