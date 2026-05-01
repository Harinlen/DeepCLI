"""BedrockProvider — Claude models via AWS Bedrock.

Uses ``AsyncAnthropicBedrock`` from the ``anthropic`` SDK (no extra
install required — the class lives in the main package).  The Bedrock
client exposes the same Messages API as the direct Anthropic client, so
the streaming implementation is shared with ``AnthropicProvider`` via
inheritance — only the client constructor and prompt-caching behavior
differ.

Differences from AnthropicProvider:
- Auth: AWS credentials (access key + secret + region), not an API key.
- Prompt caching: not supported by Bedrock — forced to False.
- Model IDs: use the Bedrock cross-region format,
  e.g. ``us.anthropic.claude-sonnet-4-6``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from anthropic import AsyncAnthropicBedrock

from kernel.llm.types import LLMChunk, Message, PromptSection, ToolSchema
from kernel.llm_provider.anthropic import AnthropicProvider

logger = logging.getLogger(__name__)


class BedrockProvider(AnthropicProvider):
    """AWS Bedrock backend.

    Inherits all streaming logic from ``AnthropicProvider``.  Overrides:

    - ``__init__``: constructs ``AsyncAnthropicBedrock`` instead of
      ``AsyncAnthropic``.
    - ``stream``: forces ``prompt_caching=False`` (Bedrock does not
      support Anthropic prompt caching).
    - ``context_window``: strips Bedrock region prefixes before lookup.

    Args:
        aws_access_key: AWS access key ID.
        aws_secret_key: AWS secret access key.
        aws_region: AWS region, e.g. ``"us-east-1"``.
    """

    def __init__(
        self,
        *,
        aws_access_key: str | None,
        aws_secret_key: str | None,
        aws_region: str | None,
    ) -> None:
        # Bypass AnthropicProvider.__init__ — it constructs AsyncAnthropic,
        # but we need AsyncAnthropicBedrock.  The parent __init__ only sets
        # self._client, so replacing it here is safe and explicit.
        object.__init__(self)

        client_kwargs: dict = {}
        if aws_access_key:
            client_kwargs["aws_access_key"] = aws_access_key
        if aws_secret_key:
            client_kwargs["aws_secret_key"] = aws_secret_key
        if aws_region:
            client_kwargs["aws_region"] = aws_region

        self._client = AsyncAnthropicBedrock(**client_kwargs)  # type: ignore[assignment]
        logger.debug("BedrockProvider: created (region=%s)", aws_region or "<default>")

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
        # Bedrock does not support Anthropic prompt caching — force off
        # regardless of what the caller requests.
        return self._stream(
            system=system,
            messages=messages,
            tool_schemas=tool_schemas,
            model_id=model_id,
            temperature=temperature,
            thinking=thinking,
            max_tokens=max_tokens,
            prompt_caching=False,
        )

    async def context_window(self, model_id: str) -> int | None:
        # Bedrock uses cross-region prefixed IDs like "us.anthropic.claude-sonnet-4-6".
        # Strip the region prefix and look up the bare model id.
        bare = model_id
        for prefix in ("us.", "eu.", "ap.", "global."):
            if model_id.startswith(prefix):
                bare = model_id[len(prefix) :]
                break
        return await super().context_window(bare)
