"""Argument + config substitution for skill bodies.

Replaces placeholders in skill Markdown content:

1. ``$ARGUMENTS`` → the entire args string (positional).
2. ``${name}`` → named argument (split from args by position).
3. ``${SKILL_DIR}`` → skill's base directory path.
4. ``${CLAUDE_SKILL_DIR}`` → same as ``${SKILL_DIR}`` (Claude Code compat).
5. ``${config.key}`` → resolved config value (Hermes pattern).

Aligned with Claude Code's ``argumentSubstitution.ts``.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any


def substitute_arguments(
    content: str,
    args: str,
    argument_names: tuple[str, ...],
    skill_dir: Path | None = None,
) -> str:
    """Replace argument placeholders in *content*.

    Parameters
    ----------
    content:
        Raw skill body text.
    args:
        User-supplied arguments as a single string.
    argument_names:
        Named parameters declared in frontmatter ``arguments``.
    skill_dir:
        Absolute path to the skill directory (for ``${SKILL_DIR}``).
    """
    # 1. $ARGUMENTS → whole args string.
    content = content.replace("$ARGUMENTS", args)

    # 2. ${name} → named arguments by position.
    if argument_names:
        try:
            parts = shlex.split(args) if args else []
        except ValueError:
            parts = args.split() if args else []
        for i, name in enumerate(argument_names):
            value = parts[i] if i < len(parts) else ""
            content = content.replace(f"${{{name}}}", value)

    # 3. ${SKILL_DIR} / ${CLAUDE_SKILL_DIR} → base directory path.
    if skill_dir is not None:
        dir_str = str(skill_dir)
        content = content.replace("${SKILL_DIR}", dir_str)
        content = content.replace("${CLAUDE_SKILL_DIR}", dir_str)

    return content


def substitute_config(content: str, config: dict[str, Any]) -> str:
    """Replace ``${config.key}`` placeholders in *content*.

    Keys not found in *config* are left as-is (no error).
    """
    if not config:
        return content

    def _replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in config:
            return str(config[key])
        return match.group(0)  # Leave unknown keys unchanged.

    return re.sub(r"\$\{config\.(\w+)\}", _replacer, content)


__all__ = ["substitute_arguments", "substitute_config"]
