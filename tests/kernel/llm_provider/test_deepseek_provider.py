from __future__ import annotations

from typing import Any, cast

import httpx
import orjson

from kernel.llm.types import (
    AssistantMessage,
    PromptSection,
    TextChunk,
    TextContent,
    ThinkingContent,
    ThoughtChunk,
    UsageChunk,
    UserMessage,
)
from kernel.llm_provider.deepseek import DeepSeekProvider


def _sse(*payloads: dict[str, object] | str) -> bytes:
    lines: list[str] = []
    for payload in payloads:
        if isinstance(payload, str):
            lines.append(f"data: {payload}\n\n")
        else:
            lines.append(f"data: {orjson.dumps(payload).decode()}\n\n")
    return "".join(lines).encode()


async def test_stream_disables_deepseek_thinking_when_kernel_thinking_is_false() -> None:
    seen_body: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_body.update(orjson.loads(request.content))
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"delta": {"content": "ok"}}]},
                "[DONE]",
            ),
        )

    provider = DeepSeekProvider(api_key="sk-test", base_url=None)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        chunks = [
            chunk
            async for chunk in provider.stream(
                system=[PromptSection(text="Be terse.")],
                messages=[UserMessage([TextContent(text="hello")])],
                tool_schemas=[],
                model_id="deepseek-v4-flash",
                temperature=0.2,
                thinking=False,
                max_tokens=123,
                prompt_caching=True,
            )
        ]
    finally:
        await provider.aclose()

    assert provider._chat_url == "https://api.deepseek.com/chat/completions"
    assert seen_body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in seen_body
    assert chunks == [TextChunk(content="ok"), UsageChunk(input_tokens=0, output_tokens=0)]


async def test_stream_enables_reasoning_and_replays_reasoning_content() -> None:
    seen_body: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_body.update(orjson.loads(request.content))
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"delta": {"reasoning_content": "think"}}]},
                {"choices": [{"delta": {"content": "answer"}}]},
                "[DONE]",
            ),
        )

    provider = DeepSeekProvider(api_key=None, base_url="https://deepseek.test")
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        chunks = [
            chunk
            async for chunk in provider.stream(
                system=[],
                messages=[
                    AssistantMessage(
                        [
                            ThinkingContent(thinking="previous reasoning", signature=""),
                            TextContent(text="previous answer"),
                        ]
                    ),
                    UserMessage([TextContent(text="continue")]),
                ],
                tool_schemas=[],
                model_id="deepseek-v4-pro",
                temperature=None,
                thinking=True,
                max_tokens=456,
                prompt_caching=False,
            )
        ]
    finally:
        await provider.aclose()

    messages = cast(list[dict[str, Any]], seen_body["messages"])
    assert seen_body["thinking"] == {"type": "enabled"}
    assert seen_body["reasoning_effort"] == "high"
    assert messages[0]["reasoning_content"] == "previous reasoning"
    assert chunks == [
        ThoughtChunk(content="think"),
        TextChunk(content="answer"),
        UsageChunk(input_tokens=0, output_tokens=0),
    ]


async def test_context_window_for_current_models() -> None:
    provider = DeepSeekProvider(api_key=None, base_url=None)
    try:
        assert await provider.context_window("deepseek-v4-pro") == 1_000_000
        assert await provider.context_window("deepseek-v4-flash") == 1_000_000
        assert await provider.context_window("deepseek-chat") == 1_000_000
        assert await provider.context_window("unknown") is None
    finally:
        await provider.aclose()
