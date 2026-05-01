"""ConfigManager section for Orchestrator user preferences.

Bound by :class:`kernel.session.SessionManager` at ``startup`` via
``ConfigManager.bind_section(file="config", section="orchestrator",
schema=OrchestratorPrefs)``.  SessionManager is the owner because
the Orchestrator is per-session (not a subsystem) — whoever builds
orchestrators holds the config.

Today's only knob is ``language`` (CC parity, prompts.ts:142-149).
Kept in its own module so new user-visible orchestrator preferences
can land here without touching the more load-bearing types in
``orchestrator/__init__.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OrchestratorPrefs(BaseModel):
    """User preferences that influence every orchestrator this kernel
    builds.

    CC mirror: ``getInitialSettings().language`` (prompts.ts:142).
    """

    language: str | None = Field(
        default=None,
        description=(
            "Preferred response language name (e.g. ``English``, "
            "``中文``, ``Français``).  When set, the Orchestrator "
            "injects CC's ``# Language`` section into every system "
            "prompt.  ``None`` — the section is omitted and the LLM "
            "chooses a language from context (CC parity)."
        ),
    )


__all__ = ["OrchestratorPrefs"]
