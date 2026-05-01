"""ACP enumeration types.

Source: ``references/acp/protocol/schema.md`` and
``references/acp/protocol/prompt-turn.md``.
"""

from __future__ import annotations

from typing import Literal

# ---------------------------------------------------------------------------
# StopReason
# ---------------------------------------------------------------------------

AcpStopReason = Literal[
    "end_turn",
    "max_tokens",
    "max_turn_requests",
    "refusal",
    "cancelled",
]

# ---------------------------------------------------------------------------
# ToolKind
# ---------------------------------------------------------------------------

AcpToolKind = Literal[
    "read",
    "edit",
    "execute",
    "search",
    "fetch",
    "think",
    "delete",
    "move",
    "other",
]

# ---------------------------------------------------------------------------
# ToolCallStatus
# ---------------------------------------------------------------------------

AcpToolCallStatus = Literal["pending", "in_progress", "completed", "failed"]

# ---------------------------------------------------------------------------
# RequestPermissionOutcome + PermissionOptionKind
# ---------------------------------------------------------------------------

AcpPermissionOutcomeTag = Literal["selected", "cancelled"]
"""Discriminator on ``RequestPermissionResponse.outcome``.

Per ACP spec (``tool-calls.md § Requesting Permission``) the response
outcome is a nested object: ``{outcome: "selected", optionId: "..."}``
for a user pick, or ``{outcome: "cancelled"}`` for a cancelled turn.
"""

AcpPermissionOptionKind = Literal[
    "allow_once",
    "allow_always",
    "reject_once",
    "reject_always",
]
"""``PermissionOption.kind`` — a hint to the client for icon / UI choice."""

# ---------------------------------------------------------------------------
# PlanEntryStatus / PlanEntryPriority
# ---------------------------------------------------------------------------

AcpPlanEntryStatus = Literal["pending", "in_progress", "completed"]
AcpPlanEntryPriority = Literal["high", "medium", "low"]
