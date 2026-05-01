"""ConfigManager section schema for the Hooks subsystem.

Sits in ``hooks.yaml`` (its own file) under section ``hooks``.  The
runtime mutation surface is currently empty — every field below is
pure metadata read at startup.  Future runtime knobs (e.g. dynamically
disable a single hook by name without restart) will land here.

Only one knob today: ``project_hooks`` is the explicit-opt-in gate
for project-layer hooks.  User-layer hooks (``~/.mustang/hooks/``)
are always loaded; project-layer hooks (``<cwd>/.mustang/hooks/``)
require the user to list each hook id in ``project_hooks.enabled``.
That way ``git clone someone-else/repo && mustang ...`` doesn't
silently inject arbitrary in-process Python.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectHooksConfig(BaseModel):
    """Project-layer opt-in list.

    A project hook directory at ``<cwd>/.mustang/hooks/<name>/`` is
    only loaded when ``<name>`` appears in ``enabled``.  Unlisted
    hooks are skipped at discovery time without error or warning.
    """

    enabled: list[str] = Field(
        default_factory=list,
        description=(
            "Hook directory names (relative to <cwd>/.mustang/hooks/) "
            "that the user has explicitly opted into for this project."
        ),
    )


class HooksConfig(BaseModel):
    """Runtime config for the Hooks subsystem.

    Bound by ``HookManager.startup`` via
    ``ConfigManager.bind_section(file="hooks", section="hooks", schema=HooksConfig)``.
    """

    project_hooks: ProjectHooksConfig = Field(default_factory=ProjectHooksConfig)


__all__ = ["HooksConfig", "ProjectHooksConfig"]
