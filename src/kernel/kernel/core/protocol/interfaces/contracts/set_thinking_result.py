"""SetThinkingResult -- contract type returned by llm/thinking_set."""

from __future__ import annotations

from pydantic import BaseModel


class SetThinkingResult(BaseModel):
    """Updated kernel-wide thinking setting."""

    enabled: bool
    """Persisted thinking setting."""
