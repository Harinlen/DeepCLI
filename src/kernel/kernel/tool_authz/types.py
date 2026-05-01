"""ToolAuthorizer data models.

Pydantic / frozen-dataclass definitions for:

- :class:`PermissionRule` — one parsed authorization rule.
- :class:`PermissionDecision` — the tagged union returned by
  ``authorize()``: allow / deny / ask.
- :class:`DecisionReason` — why the authorizer decided what it did
  (matched rule / session grant / default risk / mode override / fail-closed).
- :class:`AuthorizeContext` — per-call metadata fed into ``authorize()``.
- :class:`PermissionSuggestionBtn` — one UI button offered in the
  ``PermissionAsk.suggestions`` list.

All types are stable wire types — Session layer maps them onto ACP
``session/request_permission`` frames.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from kernel.connection_auth import AuthContext

PermissionMode = Literal["default", "plan", "bypass", "accept_edits", "auto", "dont_ask"]
"""All supported session permission modes."""


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class RuleSource(str, Enum):
    """Which config layer a rule came from."""

    USER = "user"
    PROJECT = "project"
    LOCAL = "local"
    FLAG = "flag"


class PermissionRuleValue(BaseModel):
    """The ``(tool_name, rule_content)`` pair extracted from DSL."""

    tool_name: str
    """Primary tool name or MCP prefix (e.g. ``"Bash"``, ``"mcp__slack"``)."""

    rule_content: str | None = None
    """Content inside the parens, or ``None`` for tool-level rules.

    E.g. ``"Bash(git:*)"`` → ``rule_content="git:*"``;
    ``"Bash"`` → ``rule_content=None``.
    """


class PermissionRule(BaseModel):
    """One authorization rule after parsing + layer tagging."""

    source: RuleSource
    """Which config layer this rule came from."""

    layer_index: int
    """Position within the layer.  Lower indices were declared earlier;
    ordering is preserved so rule precedence within a layer is stable."""

    rule_id: str
    """``f"{source}:{layer_index}"`` — globally unique."""

    behavior: Literal["allow", "deny", "ask"]

    value: PermissionRuleValue

    raw_dsl: str
    """Original DSL string, preserved for logging / debug."""


# ---------------------------------------------------------------------------
# DecisionReason — tagged union
# ---------------------------------------------------------------------------


class ReasonRuleMatched(BaseModel):
    """A layered PermissionRule matched."""

    type: Literal["rule"] = "rule"
    rule_id: str
    rule_behavior: Literal["allow", "deny", "ask"]
    matched_pattern: str
    layer: Literal["user", "project", "local", "flag"]


class ReasonDefaultRisk(BaseModel):
    """The Tool's own ``default_risk`` drove the decision.

    Aligned with Claude Code's ``DecisionReason.type == "other"`` variant
    (see ``types/permissions.ts:322``); we use a more descriptive name.
    """

    type: Literal["default_risk"] = "default_risk"
    risk: Literal["low", "medium", "high"]
    reason: str
    tool_name: str


class ReasonSessionGrant(BaseModel):
    """Session grant cache hit."""

    type: Literal["session_grant"] = "session_grant"
    granted_at: datetime
    signature: str


class ReasonMode(BaseModel):
    """Plan or bypass mode short-circuited the decision."""

    type: Literal["mode"] = "mode"
    mode: Literal["plan", "bypass", "accept_edits", "auto", "dont_ask"]


class ReasonNoPrompt(BaseModel):
    """``should_avoid_prompts=True`` converted an ``ask`` into a ``deny``."""

    type: Literal["no_prompt"] = "no_prompt"


class ReasonBashClassifier(BaseModel):
    """LLMJudge (BashClassifier) drove the decision."""

    type: Literal["bash_classifier"] = "bash_classifier"
    verdict: Literal["safe", "unsafe", "unknown", "budget_exceeded"]
    model_used: str | None = None


class ReasonFailClosed(BaseModel):
    """Parse error / internal exception → deny (fail-closed)."""

    type: Literal["fail_closed"] = "fail_closed"
    error_class: str


DecisionReason = (
    ReasonRuleMatched
    | ReasonDefaultRisk
    | ReasonSessionGrant
    | ReasonMode
    | ReasonNoPrompt
    | ReasonBashClassifier
    | ReasonFailClosed
)


# ---------------------------------------------------------------------------
# PermissionDecision — tagged union
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermissionSuggestionBtn:
    """One "quick decision" button surfaced in the PermissionAsk dialog."""

    label: str
    outcome: Literal["allow_once", "allow_always", "deny"]


@dataclass(frozen=True)
class PermissionAllow:
    decision_reason: DecisionReason
    updated_input: dict[str, Any] | None = None
    behavior: Literal["allow"] = "allow"


@dataclass(frozen=True)
class PermissionDeny:
    message: str
    decision_reason: DecisionReason
    behavior: Literal["deny"] = "deny"


@dataclass(frozen=True)
class PermissionAsk:
    message: str
    decision_reason: DecisionReason
    suggestions: list[PermissionSuggestionBtn] = field(default_factory=list)
    behavior: Literal["ask"] = "ask"


PermissionDecision = PermissionAllow | PermissionDeny | PermissionAsk


# ---------------------------------------------------------------------------
# AuthorizeContext — per-call metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorizeContext:
    """Per-call context handed to ``ToolAuthorizer.authorize()``.

    Built by the Orchestrator's ToolExecutor from the session's
    ``ToolContext`` + connection auth + dynamic should_avoid_prompts
    signal (see docstring of ``should_avoid_prompts`` below).
    """

    session_id: str
    """Session id — scopes the SessionGrantCache."""

    agent_depth: int
    """0 = root agent, >=1 = sub-agent."""

    mode: PermissionMode
    """Session mode.  ``plan`` forces mutating tools to deny;
    ``bypass`` forces everything to allow (operator-only);
    ``accept_edits`` auto-allows edit tools;
    ``auto`` auto-allows low-risk tool calls."""

    cwd: Path
    """Current working directory — BashTool.default_risk uses it to
    judge whether a path is inside the project."""

    connection_auth: AuthContext
    """Reference to the authenticated connection context.  Used only
    for audit logging today; reserved for future enterprise IAM
    integration (credential_type checks, etc.)."""

    should_avoid_prompts: bool = False
    """When True, every ``ask`` decision is converted to ``deny``.

    Set dynamically by the Session layer based on whether a permission
    request could actually reach a human right now:

    - Active WS connection, or interactive Gateway adapter → False
    - All WS disconnected, or non-interactive Gateway (cron/CI) → True
    - Sub-agents inherit the root session's signal (see
      ``docs/plans/landed/tool-authorizer.md`` § 11.5).

    Aligns with Claude Code's ``shouldAvoidPermissionPrompts`` field.
    """


__all__ = [
    "AuthorizeContext",
    "PermissionMode",
    "DecisionReason",
    "PermissionAllow",
    "PermissionAsk",
    "PermissionDecision",
    "PermissionDeny",
    "PermissionRule",
    "PermissionRuleValue",
    "PermissionSuggestionBtn",
    "ReasonBashClassifier",
    "ReasonDefaultRisk",
    "ReasonFailClosed",
    "ReasonMode",
    "ReasonNoPrompt",
    "ReasonRuleMatched",
    "ReasonSessionGrant",
    "RuleSource",
]
