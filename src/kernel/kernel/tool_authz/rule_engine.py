"""RuleEngine — pure function that evaluates rules against a single call.

Given ``(rules, tool, tool_input)``, produces an intermediate
``EngineOutcome`` consumed by ``ToolAuthorizer.authorize()`` to build
the final ``PermissionDecision``.

Matcher delegation — a rule with ``rule_content`` is matched by the
Tool's own ``prepare_permission_matcher`` closure; a rule without
content (``"Bash"`` form) matches every input for that tool.  MCP
server-level rules (``"mcp__slack"``) are handled specially: they
match every tool name starting with ``mcp__slack__`` without
consulting the tool's matcher.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from kernel.tools.matching import matches_name

if TYPE_CHECKING:
    from typing import Any

    from kernel.tool_authz.types import AuthorizeContext, PermissionRule
    from kernel.tools.tool import Tool
    from kernel.tools.types import PermissionSuggestion

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EngineOutcome:
    """Engine's raw output — ``ToolAuthorizer`` turns this into a
    ``PermissionDecision`` after applying mode overrides + grant cache."""

    matched_rule: PermissionRule | None
    rule_behavior: Literal["allow", "deny", "ask"] | None
    suggestion: PermissionSuggestion
    is_destructive: bool


class RuleEngine:
    """Stateless rule-traversal engine."""

    def decide(
        self,
        rules: list[PermissionRule],
        tool: Tool,
        tool_input: dict[str, Any],
        ctx: AuthorizeContext,
    ) -> EngineOutcome:
        """Walk ``rules`` and consult Tool contract methods.

        Traversal semantics:
        - First ``deny`` rule match short-circuits (highest priority).
        - Otherwise remember the first ``ask`` rule hit.
        - Otherwise remember the first ``allow`` rule hit.
        - Always call ``tool.default_risk`` and ``tool.is_destructive``
          (used downstream in arbitration).

        Aligned with Claude Code's ``hasPermissionsToUseToolInner``
        precedence (``permissions.ts:1158-1224``): deny rule > ask rule
        > default_risk > allow rule.
        """
        matched_deny: PermissionRule | None = None
        matched_ask: PermissionRule | None = None
        matched_allow: PermissionRule | None = None

        matcher_cache = None  # Lazy — only build if needed.

        for rule in rules:
            if not _rule_matches_tool(rule, tool):
                continue
            if rule.value.rule_content is None:
                # Tool-level rule (no parens) — matches every input.
                pass
            elif _is_mcp_server_rule(rule.value.tool_name, tool):
                # MCP server-level rule with content — rare but permitted.
                pass
            else:
                if matcher_cache is None:
                    matcher_cache = tool.prepare_permission_matcher(tool_input)
                if not matcher_cache(rule.value.rule_content):
                    continue

            # Record first hit at each behavior level.
            if rule.behavior == "deny" and matched_deny is None:
                matched_deny = rule
                break  # deny short-circuits — no need to keep looking
            if rule.behavior == "ask" and matched_ask is None:
                matched_ask = rule
            elif rule.behavior == "allow" and matched_allow is None:
                matched_allow = rule

        # Unconditional Tool-contract queries (cheap, pure functions).
        # AuthorizeContext satisfies the Tool ABC's RiskContext Protocol
        # (structural — both expose ``cwd`` + ``session_id``).
        suggestion = tool.default_risk(tool_input, ctx)
        destructive = tool.is_destructive(tool_input)

        # Pick the effective rule in priority order.
        if matched_deny is not None:
            return EngineOutcome(
                matched_rule=matched_deny,
                rule_behavior="deny",
                suggestion=suggestion,
                is_destructive=destructive,
            )
        if matched_ask is not None:
            return EngineOutcome(
                matched_rule=matched_ask,
                rule_behavior="ask",
                suggestion=suggestion,
                is_destructive=destructive,
            )
        if matched_allow is not None:
            return EngineOutcome(
                matched_rule=matched_allow,
                rule_behavior="allow",
                suggestion=suggestion,
                is_destructive=destructive,
            )
        return EngineOutcome(
            matched_rule=None,
            rule_behavior=None,
            suggestion=suggestion,
            is_destructive=destructive,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rule_matches_tool(rule: PermissionRule, tool: Tool) -> bool:
    """Decide whether this rule targets ``tool``.

    Handles three cases:
    - Primary name or alias equality (``"Bash"`` matches Bash + aliases).
    - MCP server-level rule (``"mcp__slack"``) matches any tool whose
      name starts with ``mcp__slack__``.
    - Wildcard ``"mcp__*"`` treated as "all MCP tools".
    """
    rule_name = rule.value.tool_name

    if matches_name(tool, rule_name):
        return True

    if rule_name == "mcp__*":
        return tool.name.startswith("mcp__")

    # Server-level MCP: two-segment rule_name.
    if rule_name.startswith("mcp__") and "__" not in rule_name[len("mcp__") :]:
        return tool.name.startswith(rule_name + "__")

    return False


def _is_mcp_server_rule(rule_tool_name: str, tool: Tool) -> bool:
    """Return True when the rule is ``mcp__server`` (no tool suffix)."""
    if not rule_tool_name.startswith("mcp__"):
        return False
    tail = rule_tool_name[len("mcp__") :]
    return "__" not in tail and tool.name.startswith(rule_tool_name + "__")


__all__ = ["EngineOutcome", "RuleEngine"]
