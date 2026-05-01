"""OpenAI-compatible provider — generic fallback for local servers.

Covers llama.cpp, Ollama, vLLM, and any endpoint that speaks the
OpenAI chat/completions API without provider-specific quirks.

For provider-specific implementations (MiniMax, DeepSeek, etc.),
see dedicated modules that subclass :class:`OpenAIBaseProvider`.

Re-exports ``_ThinkTagParser`` and ``_split_at_partial`` so existing
test imports continue to work.
"""

from __future__ import annotations

from daemon.providers.openai_base import (
    OpenAIBaseProvider,
    _ThinkTagParser,
    _split_at_partial,
)

# Re-export for backward compat (tests import from here)
__all__ = ["OpenAICompatibleProvider", "_ThinkTagParser", "_split_at_partial"]


class OpenAICompatibleProvider(OpenAIBaseProvider):
    """Generic OpenAI-compatible provider for local servers.

    Inherits all behaviour from :class:`OpenAIBaseProvider`.
    ``query_context_window()`` uses the ``/v1/models`` endpoint
    (supported by Ollama, vLLM, llama.cpp).

    For cloud providers (MiniMax, DeepSeek) that need different
    context window detection, use their dedicated subclasses.
    """

    name = "openai_compatible"
