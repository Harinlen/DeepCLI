"""RuleParser — DSL string → structured ``PermissionRule``.

DSL grammar (aligned with Claude Code ``permissionRuleParser.ts:93-152``):

    "ToolName"              → tool-level rule (matches all calls)
    "ToolName(content)"     → content-scoped rule (passed to
                              Tool.prepare_permission_matcher)
    Inside content, three characters need escaping: ``\\(``, ``\\)``, ``\\\\``.

Error handling: any parse failure produces a synthetic ``PermissionRule``
with ``tool_name="<unparsed>"`` + ``behavior="deny"``.  That rule never
matches anything (no Tool has that name), so one malformed entry
silently drops rather than killing the entire layer — but the error is
logged so the operator can fix it.
"""

from __future__ import annotations

import logging
from typing import Literal

from kernel.tool_authz.types import (
    PermissionRule,
    PermissionRuleValue,
    RuleSource,
)

logger = logging.getLogger(__name__)


def parse_rule(
    raw: str,
    behavior: Literal["allow", "deny", "ask"],
    source: RuleSource,
    layer_index: int,
) -> PermissionRule:
    """Parse one DSL string into a ``PermissionRule``.

    Always returns a rule — on parse failure, the rule's ``tool_name``
    is ``"<unparsed>"`` so it never matches any real tool and the
    malformed input is effectively neutralised without crashing the
    rule store.
    """
    rule_id = f"{source.value}:{layer_index}"
    try:
        tool_name, rule_content = _split_tool_and_content(raw)
    except ValueError as exc:
        logger.warning(
            "rule parse failed (rule_id=%s raw=%r): %s",
            rule_id,
            raw,
            exc,
        )
        return PermissionRule(
            source=source,
            layer_index=layer_index,
            rule_id=rule_id,
            behavior="deny",
            value=PermissionRuleValue(tool_name="<unparsed>"),
            raw_dsl=raw,
        )

    return PermissionRule(
        source=source,
        layer_index=layer_index,
        rule_id=rule_id,
        behavior=behavior,
        value=PermissionRuleValue(tool_name=tool_name, rule_content=rule_content),
        raw_dsl=raw,
    )


# ---------------------------------------------------------------------------
# Parser internals
# ---------------------------------------------------------------------------


def _split_tool_and_content(raw: str) -> tuple[str, str | None]:
    """Return ``(tool_name, rule_content)``; ``rule_content`` is ``None``
    when the DSL has no parens."""
    stripped = raw.strip()
    if not stripped:
        raise ValueError("empty rule")

    if "(" not in stripped:
        _validate_tool_name(stripped)
        return stripped, None

    open_idx = stripped.index("(")
    tool_name = stripped[:open_idx].strip()
    _validate_tool_name(tool_name)

    # Must end with a closing paren at the very end.
    if not stripped.endswith(")"):
        raise ValueError("missing closing paren")

    body = stripped[open_idx + 1 : -1]
    content = _unescape(body)
    if not content:
        raise ValueError("empty rule content")
    return tool_name, content


def _validate_tool_name(name: str) -> None:
    if not name:
        raise ValueError("empty tool name")
    # Match CC's permissive policy: any non-empty identifier is fine.
    # (We intentionally do not restrict to alphanumerics — MCP tool
    # names include double-underscore separators, etc.)


def _unescape(body: str) -> str:
    """Inverse of the DSL escape rules."""
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            if i + 1 >= len(body):
                raise ValueError("trailing backslash")
            nxt = body[i + 1]
            if nxt not in ("(", ")", "\\"):
                raise ValueError(f"invalid escape: \\{nxt}")
            out.append(nxt)
            i += 2
            continue
        if ch in ("(", ")"):
            raise ValueError(f"unescaped paren inside content: {ch}")
        out.append(ch)
        i += 1
    return "".join(out)


__all__ = ["parse_rule"]
