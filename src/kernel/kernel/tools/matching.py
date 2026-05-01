"""Shared tool-name matching helper.

Used by both ``ToolRegistry.lookup`` and ``ToolAuthorizer.RuleEngine``
so that aliased tools automatically inherit rules written under their
primary name — renaming a tool must not silently break user config
that still references the old name.

Aligned with Claude Code's ``toolMatchesName`` in ``Tool.ts:348-360``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kernel.tools.tool import Tool


def matches_name(tool: Tool, candidate: str) -> bool:
    """Return True when ``candidate`` matches ``tool.name`` or any alias."""
    return candidate == tool.name or candidate in tool.aliases


__all__ = ["matches_name"]
