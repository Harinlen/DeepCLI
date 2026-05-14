"""GetThinkingResult -- contract type returned by llm/thinking_get."""

from __future__ import annotations

from pydantic import BaseModel


class GetThinkingResult(BaseModel):
    """Kernel-wide thinking setting."""

    enabled: bool
    """Whether future LLM calls request provider thinking when supported."""
