"""SetDefaultModelParams -- contract type for model/set_default."""

from __future__ import annotations

from pydantic import BaseModel

from kernel.llm.config import ModelRef


class SetDefaultModelParams(BaseModel):
    """Parameters for changing the kernel-wide default model."""

    model: ModelRef
    """The model ref to set as the new default (``[provider, model_id]``)."""
