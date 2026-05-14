"""SetThinkingParams -- contract type for mutating kernel thinking policy."""

from __future__ import annotations

from pydantic import BaseModel


class SetThinkingParams(BaseModel):
    """Request to update the kernel-wide thinking setting."""

    enabled: bool
    """Whether future LLM calls should request provider thinking when supported."""
