"""AWS Bedrock provider — Claude models via AWS Bedrock Converse API.

Uses the ``anthropic`` SDK's built-in Bedrock support
(``AsyncAnthropicBedrock``), which exposes the same Messages API
surface as direct Anthropic — so message/tool/system formatting is
identical.  The only differences are authentication (AWS credentials
instead of an Anthropic API key) and model ID format
(``anthropic.claude-sonnet-4-6`` vs ``claude-sonnet-4-20250514``).

Config example (``~/.mustang/config.yaml``)::

    provider:
      default: bedrock
      bedrock:
        type: bedrock
        model: anthropic.claude-sonnet-4-6
        api_key: ${AWS_ACCESS_KEY_ID}
        aws_secret_key: ${AWS_SECRET_ACCESS_KEY}
        aws_region: us-east-1
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

from anthropic import APIError, AsyncAnthropicBedrock

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

# Bedrock model ID → context window
_BEDROCK_CONTEXT_WINDOWS: dict[str, int] = {
    "anthropic.claude-opus-4": 200_000,
    "anthropic.claude-sonnet-4": 200_000,
    "anthropic.claude-haiku-4": 200_000,
    "anthropic.claude-3-5-sonnet": 200_000,
    "anthropic.claude-3-5-haiku": 200_000,
}


def _lookup_context_window(model: str) -> int | None:
    """Best-effort context window based on the Bedrock model id prefix."""
    # Strip cross-region prefix (e.g. "us." or "global.")
    bare = model.split(".", 1)[-1] if "." in model else model
    if not bare.startswith("anthropic."):
        bare = f"anthropic.{bare}"
    for prefix, cw in _BEDROCK_CONTEXT_WINDOWS.items():
        if bare.startswith(prefix):
            return cw
    return None


# Model prefix → knowledge cutoff (after stripping region prefix).
_BEDROCK_CUTOFFS: dict[str, str] = {
    "anthropic.claude-opus-4": "Early 2025",
    "anthropic.claude-sonnet-4": "Early 2025",
    "anthropic.claude-haiku-4": "Early 2025",
    "anthropic.claude-3-5-sonnet": "Early 2024",
    "anthropic.claude-3-5-haiku": "Early 2024",
}

_BEDROCK_IDENTITY_LINES: list[str] = [
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
    bare = model.split(".", 1)[-1] if "." in model else model
    if not bare.startswith("anthropic."):
        bare = f"anthropic.{bare}"
    for prefix, cutoff in _BEDROCK_CUTOFFS.items():
        if bare.startswith(prefix):
            return cutoff
    return None


def _is_bedrock_context_overflow(exc: APIError) -> bool:
    """Return ``True`` if *exc* signals a context-window overflow."""
    msg = str(exc).lower()
    return "prompt is too long" in msg or "exceeds the maximum" in msg


class BedrockProvider(Provider):
    """Provider for Claude models via AWS Bedrock.

    Uses ``AsyncAnthropicBedrock`` from the ``anthropic`` SDK, which
    wraps the Bedrock Converse API but exposes the familiar Messages
    API surface.

    Args:
        config: Resolved provider config.  ``api_key`` is used as the
            AWS access key ID; ``aws_secret_key`` and ``aws_region``
            provide the secret key and region.
        provider_name: Logical name used in the registry.
    """

    name = "bedrock"

    def __init__(self, config: ProviderRuntimeConfig, provider_name: str = "bedrock") -> None:
        self.name = provider_name
        self._config = config
        self._default_model = config.model

        client_kwargs: dict[str, Any] = {}
        # Use explicit credentials if provided; otherwise fall back to
        # the default AWS credential chain (env vars, ~/.aws, IAM role).
        if config.api_key and config.api_key != "no-key":
            client_kwargs["aws_access_key"] = config.api_key
        if config.aws_secret_key:
            client_kwargs["aws_secret_key"] = config.aws_secret_key
        if config.aws_region:
            client_kwargs["aws_region"] = config.aws_region

        self._client = AsyncAnthropicBedrock(**client_kwargs)

    # ------------------------------------------------------------------
    # Model identity
    # ------------------------------------------------------------------

    def model_identity(self, model_id: str | None = None) -> ModelIdentity | None:
        mid = model_id or self._default_model
        cutoff = _lookup_cutoff(mid) if mid else None
        return ModelIdentity(
            knowledge_cutoff=cutoff,
            identity_lines=_BEDROCK_IDENTITY_LINES,
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
        """Stream a response from Bedrock Claude, yielding universal events.

        The streaming logic is identical to the Anthropic provider since
        ``AsyncAnthropicBedrock`` exposes the same event types.
        """
        model = model or self._default_model
        # Bedrock does not support prompt caching
        prompt_caching = False

        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages_to_anthropic(messages),
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

        create_kwargs.update(kwargs)

        # Per-tool-block accumulation state.
        tool_blocks: dict[int, dict[str, Any]] = {}
        usage_input = 0
        usage_output = 0

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
                        usage = getattr(event, "usage", None)
                        if usage is not None:
                            usage_output = getattr(usage, "output_tokens", usage_output)

                    elif etype == "message_start":
                        msg = getattr(event, "message", None)
                        if msg is not None:
                            usage = getattr(msg, "usage", None)
                            if usage is not None:
                                usage_input = getattr(usage, "input_tokens", usage_input)

        except APIError as exc:
            if _is_bedrock_context_overflow(exc):
                raise PromptTooLongError(str(exc)) from exc
            logger.error("Bedrock API error: %s", exc)
            yield StreamError(message=str(exc))
            return
        except Exception as exc:
            raise ProviderError(f"Unexpected error calling {self.name}: {exc}") from exc

        yield StreamEnd(
            usage=UsageInfo(
                input_tokens=usage_input,
                output_tokens=usage_output,
            )
        )

    # ------------------------------------------------------------------
    # Model info
    # ------------------------------------------------------------------

    async def query_context_window(self) -> int | None:
        return _lookup_context_window(self._default_model)

    async def models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id=self._default_model,
                name=self._default_model,
                provider=self.name,
                supports_tools=True,
            )
        ]


def _parse_tool_args(raw: str) -> dict[str, Any]:
    """Parse a partial-JSON accumulation into a dict; empty -> ``{}``."""
    import json

    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    except json.JSONDecodeError:
        return {"_raw": raw}


__all__ = ["BedrockProvider"]
