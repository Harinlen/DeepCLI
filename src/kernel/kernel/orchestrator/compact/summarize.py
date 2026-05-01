"""LLM-driven summarisation for compaction."""

from __future__ import annotations

import logging

from kernel.llm.types import (
    Message,
    PromptSection,
    StreamError,
    TextChunk,
    TextContent,
    UserMessage,
)
from kernel.orchestrator.compact.render import render_messages

logger = logging.getLogger(__name__)


class Summarizer:
    """Call the configured LLM provider to summarise old messages.

    The class accepts ``object`` dependencies because several unit tests provide
    lightweight fakes.  Attribute probes are kept inside this module so the rest
    of compaction can use a narrow, predictable facade.
    """

    def __init__(self, deps: object, model: object) -> None:
        """Create an LLM summariser.

        Args:
            deps: Orchestrator deps or a minimal test double.
            model: Default model reference used if no compact role is available.
        """
        self._deps = deps
        self._model = _resolve_compact_model(deps, model)
        self._system, self._prefix, self._fallback = _load_prompts(deps)

    async def summarise(self, messages: list[Message]) -> str:
        """Return a plain-text summary of ``messages``.

        Args:
            messages: Old conversation prefix selected for replacement.

        Returns:
            Provider-generated summary text, or the configured fallback when the
            provider is unavailable or streams no usable text.
        """
        provider = getattr(self._deps, "provider", None)
        if provider is None:
            return self._fallback
        request = [
            UserMessage(content=[TextContent(text=self._prefix + render_messages(messages))])
        ]

        parts: list[str] = []
        try:
            async for chunk in await provider.stream(
                system=[self._system],
                messages=request,
                tool_schemas=[],
                model=self._model,
                temperature=None,
            ):
                if isinstance(chunk, TextChunk):
                    parts.append(chunk.content)
                elif isinstance(chunk, StreamError):
                    logger.warning(
                        "Compactor: stream error during summarisation: %s", chunk.message
                    )
                    break
        except Exception as exc:
            logger.warning("Compactor: provider error during summarisation: %s", exc)

        return "".join(parts).strip() or self._fallback


def _resolve_compact_model(deps: object, model: object) -> object:
    """Prefer the provider's compact role while preserving default-model tests.

    Args:
        deps: Dependency bundle or test double that may expose ``provider``.
        model: Fallback model reference.

    Returns:
        Compact-role model when available, otherwise ``model``.
    """
    provider = getattr(deps, "provider", None)
    resolve = getattr(provider, "model_for_or_default", None)
    if callable(resolve):
        try:
            return resolve("compact")
        except Exception:
            return model
    return model


def _load_prompts(deps: object) -> tuple[PromptSection, str, str]:
    """Load prompt text from PromptManager, falling back for minimal test deps.

    Args:
        deps: Dependency bundle or test double that may expose ``prompts``.

    Returns:
        ``(system_section, user_prefix, fallback_summary)``.
    """
    prompts = getattr(deps, "prompts", None)
    if prompts is not None:
        return (
            PromptSection(text=prompts.get("orchestrator/compact_system"), cache=False),
            prompts.get("orchestrator/compact_prefix"),
            prompts.get("orchestrator/compact_fallback"),
        )
    return (
        PromptSection(
            text=(
                "You are a conversation summariser. Summarise the provided "
                "conversation concisely, preserving all key facts, decisions, "
                "file paths, code snippets, and context needed to continue."
            ),
            cache=False,
        ),
        "Summarise the following conversation:\n\n",
        "[Conversation history compacted — summary unavailable]",
    )
