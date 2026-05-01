"""Helpers for Memory subsystem calls into the LLM stream interface."""

from __future__ import annotations

from typing import Any

from kernel.llm.types import PromptSection, StreamError, TextChunk, TextContent, UserMessage


async def collect_llm_text(
    llm: Any,
    *,
    model: Any,
    prompt: str,
    system_text: str,
    temperature: float | None = 0.0,
    max_tokens: int | None = 2000,
) -> str:
    """Call an LLMManager-compatible provider and concatenate text chunks.

    Args:
        llm: Object implementing the current ``LLMManager.stream`` contract.
        model: Model reference resolved by ``LLMManager.model_for*``.
        prompt: User prompt text.
        system_text: Non-empty system section for providers that require one.
        temperature: Sampling temperature passed through to the provider.
        max_tokens: Optional response-token override.

    Returns:
        Concatenated text content from the streaming response.

    Raises:
        RuntimeError: If the provider yields a recoverable stream error chunk.
    """
    stream = llm.stream(
        system=[PromptSection(text=system_text, cache=False)],
        messages=[UserMessage(content=[TextContent(text=prompt)])],
        tool_schemas=[],
        model=model,
        temperature=temperature,
        thinking=False,
        max_tokens=max_tokens,
    )
    if hasattr(stream, "__await__"):
        stream = await stream

    chunks: list[str] = []
    async for event in stream:
        if isinstance(event, TextChunk):
            chunks.append(event.content)
            continue
        if isinstance(event, StreamError):
            raise RuntimeError(f"Memory LLM stream error: {event.message}")
        text = getattr(event, "text", None)
        if isinstance(text, str):
            chunks.append(text)
    return "".join(chunks)
