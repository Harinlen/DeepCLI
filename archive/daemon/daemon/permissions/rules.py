"""Tool rule parser — shared by hook ``if`` conditions and permission rules.

Parses ``ToolName(pattern)`` syntax into a ``ToolRule`` and provides
a ``matches()`` function that checks whether a tool call matches.

Examples::

    "Bash(rm *)"      → ToolRule(tool_name="bash", pattern="rm *")
    "Bash"            → ToolRule(tool_name="bash", pattern=None)      # any args
    "*"               → ToolRule(tool_name="*", pattern=None)         # any tool
    "file_read(*.py)" → ToolRule(tool_name="file_read", pattern="*.py")
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Any

# Matches: ToolName(pattern) or ToolName or *
_RULE_RE = re.compile(
    r"^(?P<tool>[A-Za-z_*][A-Za-z0-9_*]*)"  # tool name (or *)
    r"(?:\((?P<pattern>.*)\))?$"  # optional (pattern)
)


@dataclass(frozen=True, slots=True)
class ToolRule:
    """Parsed tool-matching rule.

    Attributes:
        tool_name: Tool name to match (case-insensitive), or ``"*"`` for any.
        pattern: Glob pattern to match against tool input, or ``None``
            to match any input.
    """

    tool_name: str
    pattern: str | None = None


@dataclass(frozen=True, slots=True)
class PermissionRule:
    """A :class:`ToolRule` tagged with its effect (allow / deny).

    Wraps a :class:`ToolRule` with an ``effect`` field so the engine
    can store allow- and deny-lists uniformly.  The original rule
    string is preserved for round-tripping to ``settings.json`` and
    for de-duplication on insert.

    Attributes:
        tool_rule: Parsed tool-matching rule (reuses existing logic).
        effect: Either ``"allow"`` or ``"deny"``.
        rule_str: Original rule text (e.g. ``"Bash(git *)"``).
    """

    tool_rule: ToolRule
    effect: str
    rule_str: str


def parse_rule(rule_str: str) -> ToolRule:
    """Parse a ``ToolName(pattern)`` string into a :class:`ToolRule`.

    Args:
        rule_str: The rule string to parse.

    Returns:
        Parsed ToolRule.

    Raises:
        ValueError: If the rule string is malformed.
    """
    rule_str = rule_str.strip()
    if not rule_str:
        raise ValueError("Empty rule string")

    m = _RULE_RE.match(rule_str)
    if m is None:
        raise ValueError(f"Malformed rule: {rule_str!r}")

    tool_name = m.group("tool").lower()
    pattern = m.group("pattern")  # None if no parens

    return ToolRule(tool_name=tool_name, pattern=pattern)


def _extract_first_string_value(tool_input: dict[str, Any]) -> str | None:
    """Extract the first string value from a tool input dict.

    Used for glob-matching the hook ``if`` pattern against the primary
    argument of a tool call (e.g. ``command`` for Bash, ``pattern``
    for Grep).

    Args:
        tool_input: Tool call arguments dict.

    Returns:
        First string value found, or ``None`` if no string values.
    """
    for v in tool_input.values():
        if isinstance(v, str):
            return v
    return None


def matches(rule: ToolRule, tool_name: str, tool_input: dict[str, Any]) -> bool:
    """Check whether a tool call matches a rule.

    Matching logic:
      1. ``rule.tool_name == "*"`` matches any tool.
      2. Otherwise, case-insensitive exact match on tool name.
      3. If ``rule.pattern is None``, any input matches.
      4. Otherwise, ``fnmatch`` the pattern against the first string
         value in the tool input dict.

    Args:
        rule: The parsed rule to check.
        tool_name: Name of the tool being called.
        tool_input: Arguments dict of the tool call.

    Returns:
        True if the tool call matches the rule.
    """
    # Tool name check
    if rule.tool_name != "*" and rule.tool_name != tool_name.lower():
        return False

    # No pattern → matches any input
    if rule.pattern is None:
        return True

    # Match pattern against first string value in input
    first_str = _extract_first_string_value(tool_input)
    if first_str is None:
        return False

    return fnmatch.fnmatch(first_str, rule.pattern)
