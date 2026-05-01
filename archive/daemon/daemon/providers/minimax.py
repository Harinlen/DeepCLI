"""MiniMax provider — cloud LLM via MiniMax's OpenAI-compatible API.

MiniMax (api.minimax.io) speaks the OpenAI chat/completions protocol
but does NOT support the ``/v1/models`` endpoint.  Context window
detection falls back to the OpenRouter public API.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from daemon.providers.openai_base import OpenAIBaseProvider

logger = logging.getLogger(__name__)

# OpenRouter's public model index — no auth required.
_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Timeout for the OpenRouter metadata query (seconds).
_OPENROUTER_TIMEOUT = 10


async def _query_openrouter_context_window(model: str) -> int | None:
    """Query context window from the OpenRouter public API.

    OpenRouter maintains metadata for all major models including
    ``context_length``.  Model names are matched with a
    ``minimax/{model}`` pattern (e.g. ``minimax/minimax-m2.7``).

    Args:
        model: Model ID as configured (e.g. ``"MiniMax-M2.7"``).

    Returns:
        Context window in tokens, or ``None`` if not found.
    """
    # OpenRouter model IDs use the pattern "minimax/{model_lowercase}"
    lookup_id = f"minimax/{model.lower()}"

    try:
        async with httpx.AsyncClient(timeout=_OPENROUTER_TIMEOUT) as client:
            resp = await client.get(_OPENROUTER_MODELS_URL)
            resp.raise_for_status()

        data: list[dict[str, Any]] = resp.json().get("data", [])
        for entry in data:
            if entry.get("id") == lookup_id:
                ctx = entry.get("context_length")
                if isinstance(ctx, int) and ctx > 0:
                    logger.info(
                        "MiniMax context window from OpenRouter: %s = %d tokens",
                        model,
                        ctx,
                    )
                    return ctx

        logger.debug("Model %r not found on OpenRouter (tried %r)", model, lookup_id)
    except Exception:
        logger.debug("Failed to query OpenRouter for model %r", model, exc_info=True)

    return None


class MiniMaxProvider(OpenAIBaseProvider):
    """Provider for MiniMax cloud API (api.minimax.io).

    Inherits streaming and message translation from
    :class:`OpenAIBaseProvider`.  Overrides ``query_context_window()``
    to use the OpenRouter public API since MiniMax does not expose a
    ``/v1/models`` endpoint.
    """

    name = "minimax"

    async def query_context_window(self) -> int | None:
        """Detect context window via OpenRouter public API.

        MiniMax's API returns 404 on ``/v1/models``, so we query
        OpenRouter's public model index instead.

        Returns:
            Context window in tokens, or ``None`` if unavailable.
        """
        return await _query_openrouter_context_window(self._default_model)
