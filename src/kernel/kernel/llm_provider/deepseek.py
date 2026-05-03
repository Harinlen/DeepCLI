"""DeepSeekProvider — DeepSeek API via OpenAI-compatible Chat Completions."""

from __future__ import annotations

from kernel.llm.types import Message, PromptSection, ToolSchema
from kernel.llm_provider.format.openai import messages_to_openai
from kernel.llm_provider.openai_compatible import OpenAICompatibleProvider

_DEFAULT_BASE_URL = "https://api.deepseek.com"
_CONTEXT_WINDOWS: dict[str, int] = {
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro": 1_000_000,
    "deepseek-chat": 1_000_000,
    "deepseek-reasoner": 1_000_000,
}


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek backend using the provider's OpenAI-compatible endpoint.

    DeepSeek defaults thinking mode to enabled, so this provider always
    sends an explicit toggle.  When thinking is enabled, replayed assistant
    thinking is serialized as ``reasoning_content`` for tool-call subturns.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url or _DEFAULT_BASE_URL,
        )

    def _request_body(
        self,
        *,
        system: list[PromptSection],
        messages: list[Message],
        tool_schemas: list[ToolSchema],
        model_id: str,
        temperature: float | None,
        thinking: bool,
        max_tokens: int,
    ) -> dict[str, object]:
        body = super()._request_body(
            system=system,
            messages=messages,
            tool_schemas=tool_schemas,
            model_id=model_id,
            temperature=temperature,
            thinking=thinking,
            max_tokens=max_tokens,
        )
        body["thinking"] = {"type": "enabled" if thinking else "disabled"}
        if thinking:
            body["reasoning_effort"] = "high"
        return body

    def _messages_to_api(
        self,
        messages: list[Message],
        system: list[PromptSection],
    ) -> list[dict[str, object]]:
        return messages_to_openai(
            messages,
            system,
            include_reasoning_content=True,
        )

    async def context_window(self, model_id: str) -> int | None:
        return _CONTEXT_WINDOWS.get(model_id)
