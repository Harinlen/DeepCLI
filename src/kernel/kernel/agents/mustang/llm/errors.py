"""LLM layer error types."""

from __future__ import annotations


class ModelNotFoundError(KeyError):
    """Raised by ``LLMManager._resolve()`` when a model ref is unknown.

    ``model_ref`` is the alias or key that was not found.
    ``known`` is the sorted list of valid model keys at the time of the call.
    """

    def __init__(self, model_ref: str, *, known: list[str]) -> None:
        self.model_ref = model_ref
        self.known = known
        super().__init__(f"Model '{model_ref}' not found. Known models: {known}")
