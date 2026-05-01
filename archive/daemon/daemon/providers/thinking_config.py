"""Parser + type for the ``provider.thinking`` config field.

The user writes one of:

- ``"adaptive"`` — let the provider pick (default for reasoning
  models like Claude 4).
- ``"disabled"`` — opt out entirely.
- integer ``budget_tokens`` — enable with an explicit budget
  (Anthropic expects 1024–32_000).

The Anthropic provider consumes this via :func:`to_anthropic_param`
which returns ``None`` (omit the parameter) or the
``{"type": ..., "budget_tokens": ...}`` dict the SDK expects.
"""

from __future__ import annotations

from typing import Any, Literal

ThinkingMode = Literal["adaptive", "disabled", "enabled"]

_MIN_BUDGET = 1024
_MAX_BUDGET = 32_000
_ADAPTIVE_BUDGET = 4096  # sensible default when mode is "adaptive"


def parse_thinking(raw: str | int | None) -> tuple[ThinkingMode, int]:
    """Normalise the raw config value to ``(mode, budget)``.

    Unknown strings fall back to ``"adaptive"`` — the daemon must
    start even if the user typo'd.
    """
    if raw is None:
        return "adaptive", _ADAPTIVE_BUDGET
    if isinstance(raw, int):
        budget = max(_MIN_BUDGET, min(_MAX_BUDGET, raw))
        return "enabled", budget
    lowered = raw.strip().lower()
    if lowered == "disabled":
        return "disabled", 0
    if lowered == "adaptive":
        return "adaptive", _ADAPTIVE_BUDGET
    if lowered == "enabled":
        return "enabled", _ADAPTIVE_BUDGET
    return "adaptive", _ADAPTIVE_BUDGET


def to_anthropic_param(raw: str | int | None) -> dict[str, Any] | None:
    """Return the ``thinking`` kwarg for Anthropic Messages API.

    ``None`` means omit the parameter (adaptive — let Anthropic decide
    based on model defaults).
    """
    mode, budget = parse_thinking(raw)
    if mode == "disabled":
        return {"type": "disabled"}
    if mode == "enabled":
        return {"type": "enabled", "budget_tokens": budget}
    # adaptive: omit and let the model's default behaviour apply.
    return None


__all__ = ["ThinkingMode", "parse_thinking", "to_anthropic_param"]
