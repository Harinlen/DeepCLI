"""SetCurrentModelParams -- contract type for model/set_current."""

from __future__ import annotations

from pydantic import BaseModel

from kernel.llm.config import ModelRef


class SetCurrentModelParams(BaseModel):
    """Parameters for changing one ``llm.current_used`` role."""

    model: ModelRef
    """The model ref to assign to the role (``[provider, model_id]``)."""

    role: str = "default"
    """Current-used role to update.  Defaults to ``default``."""
