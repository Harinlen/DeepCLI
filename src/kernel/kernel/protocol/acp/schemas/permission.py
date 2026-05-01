"""ACP ``session/request_permission`` wire-format schemas.

Per ACP spec (``docs/kernel/references/acp/protocol/tool-calls.md``
§ Requesting Permission):

Request::

    {
      "params": {
        "sessionId": "...",
        "toolCall": { "toolCallId": "call_001", ...optional fields },
        "options": [
          { "optionId": "allow_once", "name": "Allow once", "kind": "allow_once" },
          ...
        ]
      }
    }

Response::

    {
      "result": {
        "outcome": { "outcome": "selected", "optionId": "allow_once" }
      }
    }

    # or cancelled
    { "result": { "outcome": { "outcome": "cancelled" } } }

Field renames from earlier Mustang drafts:
  - ``PermissionOption.id`` → ``option_id`` (wire: ``optionId``)
  - ``PermissionOption.title`` → ``name``
  - ``RequestPermissionResponse.outcome`` now a nested discriminated
    model rather than a flat enum.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from kernel.protocol.acp.schemas.base import AcpModel
from kernel.protocol.acp.schemas.enums import AcpPermissionOptionKind


class ToolCallUpdate(AcpModel):
    """The ``toolCall`` field of ``RequestPermissionRequest``.

    Only ``tool_call_id`` is required (matches ACP
    ``ToolCallUpdate``); clients reassemble richer metadata from prior
    ``session/update`` frames.  Optional fields below carry enough
    context for a permission-only client (no prior tool_call_start
    stream) to render a meaningful dialog.
    """

    tool_call_id: str
    title: str | None = None
    kind: str | None = None
    input_summary: str | None = None


class PermissionOption(AcpModel):
    """One option presented to the user in the permission dialog."""

    option_id: str
    name: str
    kind: AcpPermissionOptionKind


class PermissionOutcomeSelected(AcpModel):
    """User picked one of the options."""

    outcome: Literal["selected"] = "selected"
    option_id: str
    updated_input: dict[str, Any] | None = None
    """Optional rewritten tool input returned by the client.

    Used by ``AskUserQuestionTool``: the client injects the user's
    answers into ``updated_input`` so the tool receives them via
    ``PermissionResponse.updated_input → PermissionAllow.updated_input``.
    """


class PermissionOutcomeCancelled(AcpModel):
    """Prompt turn was cancelled while the permission dialog was open."""

    outcome: Literal["cancelled"] = "cancelled"


# Discriminated union on the ``outcome`` literal field.
PermissionOutcome = PermissionOutcomeSelected | PermissionOutcomeCancelled


class RequestPermissionRequest(AcpModel):
    session_id: str
    tool_call: ToolCallUpdate
    options: list[PermissionOption]
    tool_input: dict[str, Any] | None = None
    """The original tool input dict.  Included so the client can render
    tool-specific UIs (e.g. ``AskUserQuestionTool`` sends its ``questions``
    array here) without needing to parse earlier ``session/update`` frames.
    """
    meta: dict[str, Any] | None = None


class RequestPermissionResponse(AcpModel):
    outcome: PermissionOutcome = Field(discriminator="outcome")
    meta: dict[str, Any] | None = None
