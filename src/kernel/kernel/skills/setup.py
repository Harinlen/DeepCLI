"""Environment setup check for skills (Hermes pattern).

Claude Code's ``requires.env`` only does a boolean check (present /
absent) and skips ineligible skills silently.  Hermes provides a
richer flow: when required environment variables are missing, the
skill returns a guided setup message instead of silently failing.

This module checks ``setup.env`` entries and generates a human-readable
setup message that SkillTool returns to the LLM, which then prompts
the user to provide the missing values.
"""

from __future__ import annotations

import os

from kernel.skills.types import SkillManifest


def check_setup(manifest: SkillManifest) -> tuple[bool, str | None]:
    """Check whether the skill's required environment variables are set.

    Returns ``(True, None)`` when all required vars are present (or
    when the skill has no ``setup.env`` declaration).

    Returns ``(False, message)`` when one or more required vars are
    missing.  The message is a formatted guide for the LLM to relay
    to the user.
    """
    if manifest.setup is None or not manifest.setup.env:
        return True, None

    missing: list[str] = []
    for entry in manifest.setup.env:
        value = os.environ.get(entry.name)
        if value:
            continue  # Present and non-empty.
        if entry.optional and entry.default is not None:
            continue  # Optional with default — fine.
        if entry.optional:
            continue  # Optional without default — also fine.
        # Required and missing.
        missing.append(entry.name)

    if not missing:
        return True, None

    # Build the setup message.
    lines = [f'Skill "{manifest.name}" requires environment setup:\n']
    for entry in manifest.setup.env:
        is_missing = entry.name in missing
        status = "(required)" if not entry.optional else "(optional)"
        if entry.optional and entry.default is not None:
            status = f"(optional, default: {entry.default})"
        marker = "  **MISSING** " if is_missing else "  "

        lines.append(f"{marker}{entry.name} {status}")
        lines.append(f"    {entry.prompt}")
        if entry.help:
            lines.append(f"    Help: {entry.help}")
        lines.append("")

    lines.append("Set these in your environment or ~/.mustang/config.yaml, then retry.")

    return False, "\n".join(lines)


__all__ = ["check_setup"]
