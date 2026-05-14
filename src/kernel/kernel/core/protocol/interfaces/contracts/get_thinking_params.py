"""GetThinkingParams -- contract type for reading kernel thinking policy."""

from __future__ import annotations

from pydantic import BaseModel


class GetThinkingParams(BaseModel):
    """Empty params for reading the kernel-wide thinking setting."""
