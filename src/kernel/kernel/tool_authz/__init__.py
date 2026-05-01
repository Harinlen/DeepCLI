"""ToolAuthorizer subsystem — per-tool-call permission decisions.

Public surface:

- :class:`ToolAuthorizer` — the Subsystem loaded at step 3 of the kernel
  lifespan.
- :class:`AuthorizeContext` — per-call context passed into ``authorize()``.
- :class:`PermissionDecision` — the tagged union returned by ``authorize()``.
- Supporting types: :class:`PermissionAllow` / :class:`PermissionDeny` /
  :class:`PermissionAsk` / the :class:`DecisionReason` union.

See ``docs/plans/landed/tool-authorizer.md`` for the full design.
"""

from __future__ import annotations

from kernel.tool_authz.authorizer import ToolAuthorizer
from kernel.tool_authz.constants import BASH_TOOL_NAME, POWERSHELL_TOOL_NAME, SHELL_TOOL_NAMES
from kernel.tool_authz.types import (
    AuthorizeContext,
    DecisionReason,
    PermissionAllow,
    PermissionAsk,
    PermissionDecision,
    PermissionDeny,
    PermissionMode,
    PermissionRule,
    PermissionRuleValue,
    PermissionSuggestionBtn,
    ReasonBashClassifier,
    ReasonDefaultRisk,
    ReasonFailClosed,
    ReasonMode,
    ReasonNoPrompt,
    ReasonRuleMatched,
    ReasonSessionGrant,
    RuleSource,
)

__all__ = [
    "BASH_TOOL_NAME",
    "POWERSHELL_TOOL_NAME",
    "SHELL_TOOL_NAMES",
    "AuthorizeContext",
    "DecisionReason",
    "PermissionAllow",
    "PermissionAsk",
    "PermissionDecision",
    "PermissionDeny",
    "PermissionMode",
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
    "ToolAuthorizer",
]
