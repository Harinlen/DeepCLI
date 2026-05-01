"""Tests for Memory subsystem LLM stream helper."""

from __future__ import annotations

from typing import Any

import pytest

from kernel.llm.types import PromptSection, TextChunk, UserMessage
from kernel.memory.llm_text import collect_llm_text


class StrictLLM:
    """Fake with the same required keyword-only shape as LLMManager.stream."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def stream(
        self,
        *,
        system: list[PromptSection],
        messages: list[UserMessage],
        tool_schemas: list[Any],
        model: Any,
        temperature: float | None,
        thinking: bool = False,
        max_tokens: int | None = None,
    ) -> Any:
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "tool_schemas": tool_schemas,
                "model": model,
                "temperature": temperature,
                "thinking": thinking,
                "max_tokens": max_tokens,
            }
        )

        async def chunks() -> Any:
            yield TextChunk("hello")
            yield TextChunk(" memory")

        return chunks()


@pytest.mark.asyncio
async def test_collect_llm_text_uses_current_stream_contract() -> None:
    llm = StrictLLM()
    result = await collect_llm_text(
        llm,
        model="provider/model",
        prompt="score memories",
        system_text="Score memory relevance.",
        temperature=0.0,
        max_tokens=128,
    )

    assert result == "hello memory"
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["system"] == [PromptSection(text="Score memory relevance.", cache=False)]
    assert call["messages"][0].content[0].text == "score memories"
    assert call["tool_schemas"] == []
    assert call["model"] == "provider/model"
    assert call["temperature"] == 0.0
    assert call["thinking"] is False
    assert call["max_tokens"] == 128
