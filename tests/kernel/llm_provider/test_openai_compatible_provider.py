from __future__ import annotations

import httpx
import orjson
import pytest

from kernel.llm.types import (
    PromptSection,
    TextChunk,
    TextContent,
    ToolSchema,
    ToolUseChunk,
    UsageChunk,
    UserMessage,
    StreamError,
)
from kernel.llm_provider.errors import PromptTooLongError, ProviderError
from kernel.llm_provider.openai_compatible import OpenAICompatibleProvider


def _sse(*payloads: dict[str, object] | str) -> bytes:
    lines: list[str] = []
    for payload in payloads:
        if isinstance(payload, str):
            lines.append(f"data: {payload}\n\n")
        else:
            lines.append(f"data: {orjson.dumps(payload).decode()}\n\n")
    return "".join(lines).encode()


async def _collect(provider: OpenAICompatibleProvider) -> list[object]:
    chunks: list[object] = []
    async for chunk in provider.stream(
        system=[PromptSection(text="Be terse.")],
        messages=[UserMessage([TextContent(text="hello")])],
        tool_schemas=[ToolSchema(name="Echo", description="echo", input_schema={"type": "object"})],
        model_id="fake-model",
        temperature=0.2,
        thinking=True,
        max_tokens=123,
        prompt_caching=True,
    ):
        chunks.append(chunk)
    return chunks


async def test_stream_emits_text_tool_use_and_usage() -> None:
    seen_body: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_body.update(orjson.loads(request.content))
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"delta": {"content": "hi"}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "function": {"name": "Echo", "arguments": '{"text"'},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": ':"ok"}'},
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
                {
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 7,
                        "prompt_tokens_details": {"cached_tokens": 3},
                    },
                    "choices": [],
                },
                "[DONE]",
            ),
        )

    provider = OpenAICompatibleProvider(api_key="sk-test", base_url="https://fake.local/v1")
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        chunks = await _collect(provider)
    finally:
        await provider.aclose()

    assert seen_body["model"] == "fake-model"
    assert seen_body["temperature"] == 0.2
    assert seen_body["stream"] is True
    assert seen_body["tools"][0]["function"]["name"] == "Echo"  # type: ignore[index]
    assert chunks[0] == TextChunk(content="hi")
    assert chunks[1] == ToolUseChunk(id="call-1", name="Echo", input={"text": "ok"})
    assert chunks[2] == UsageChunk(input_tokens=5, output_tokens=7, cache_read_tokens=3)


async def test_stream_flushes_unfinished_tool_call_with_invalid_json_as_empty_input() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "function": {"name": "Broken", "arguments": "{"},
                                    }
                                ]
                            }
                        }
                    ]
                },
                "[DONE]",
            ),
        )

    provider = OpenAICompatibleProvider(api_key=None, base_url="https://fake.local/v1/")
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        chunks = await _collect(provider)
    finally:
        await provider.aclose()

    assert chunks[0] == ToolUseChunk(id="call-1", name="Broken", input={})
    assert isinstance(chunks[1], UsageChunk)


@pytest.mark.parametrize(
    ("status", "exc_type", "message"),
    [
        (401, ProviderError, "auth error"),
        (413, PromptTooLongError, "Prompt too long"),
        (500, ProviderError, "HTTP 500"),
    ],
)
async def test_stream_raises_for_unrecoverable_http_statuses(
    status: int,
    exc_type: type[Exception],
    message: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=b"upstream exploded")

    provider = OpenAICompatibleProvider(api_key=None, base_url="https://fake.local/v1")
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(exc_type, match=message):
            await _collect(provider)
    finally:
        await provider.aclose()


async def test_stream_yields_stream_error_for_transport_failures() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("cannot connect", request=request)

    provider = OpenAICompatibleProvider(api_key=None, base_url="https://fake.local/v1")
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        chunks = await _collect(provider)
    finally:
        await provider.aclose()

    assert chunks == [StreamError(message="cannot connect", code="transient_transport")]


async def test_stream_yields_transient_error_when_sse_ends_without_done() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse({"choices": [{"delta": {"content": "partial"}}]}),
        )

    provider = OpenAICompatibleProvider(api_key=None, base_url="https://fake.local/v1")
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        chunks = await _collect(provider)
    finally:
        await provider.aclose()

    assert chunks == [
        TextChunk(content="partial"),
        StreamError(message="stream ended before [DONE]", code="transient_transport"),
    ]


async def test_discover_models_handles_success_non_200_and_exceptions() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            assert str(request.url) == "https://fake.local/v1/models"
            return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})
        if calls == 2:
            return httpx.Response(503, json={"error": "busy"})
        raise httpx.ReadError("broken", request=request)

    provider = OpenAICompatibleProvider(api_key=None, base_url="https://fake.local/v1")
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        assert await provider.models() == []
        assert await provider.discover_models() == ["a", "b"]
        assert await provider.discover_models() == []
        assert await provider.discover_models() == []
    finally:
        await provider.aclose()
