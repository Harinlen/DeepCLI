"""ConfigManager section schema for the Skills subsystem.

Sits in ``skills.yaml`` under section ``skills``.  The runtime
mutation surface is currently empty — every field below is pure
metadata read at startup.

Today's knobs:

``claude_compat``
    Opt-in switch for scanning ``.claude/skills/`` directories
    alongside the native ``.mustang/skills/`` roots.  **Default:
    False.**  Rationale: ``.claude/skills/`` is a Claude-Code-CLI
    convention that developers working on this repo can populate
    with session-scoped workflow skills (e.g. ``/done-check``).
    Those dev-workflow skills are not meaningful to DeepCLI's LLM
    when a real user is running the kernel, so the default behaviour
    is to ignore them.  Users who genuinely want to reuse their
    Claude Code user-level skills inside DeepCLI can set this to
    True — see ``docs/kernel/subsystems/skills.md`` §Claude Code
    skill 兼容.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillsConfig(BaseModel):
    """Runtime config for the Skills subsystem.

    Bound by ``SkillManager.startup`` via
    ``ConfigManager.bind_section(file="skills", section="skills", schema=SkillsConfig)``.
    """

    claude_compat: bool = Field(
        default=False,
        description=(
            "When True, scan ``.claude/skills/`` (user + project "
            "layers) in addition to ``.mustang/skills/``.  Default "
            "False — opt-in because Claude-Code-session-only "
            "workflow skills in that directory are not DeepCLI "
            "features and would otherwise pollute the LLM's skill "
            "listing and the /command autocomplete surface."
        ),
    )


__all__ = ["SkillsConfig"]
