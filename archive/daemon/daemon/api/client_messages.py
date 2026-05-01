"""Typed inbound WebSocket messages.

Mirror of :mod:`daemon.engine.stream` for the client → daemon
direction.  Every frame the CLI (or any other client) sends is
parsed into one of the models below; the WS handler then pattern-
matches on the concrete class to dispatch.

Adding a new client message:
  1. Declare a frozen ``BaseModel`` with a unique ``type`` literal.
  2. Add it to the :data:`ClientMessage` union.
  3. Add a ``case`` arm in :func:`daemon.api.ws.websocket_endpoint`.

This replaces a 13-branch ``if msg_type == "…"`` chain (see the
Phase 4.X audit in ``docs/lessons-learned.md``) and moves inbound
payload validation from ad-hoc ``data.get("field", default")``
reads into the Pydantic layer, so malformed messages are rejected
with a single parse point.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator


# ---------------------------------------------------------------------------
# Simple request messages
# ---------------------------------------------------------------------------


class UserMessage(BaseModel):
    """User-typed prompt sent to the orchestrator."""

    type: Literal["user_message"] = "user_message"
    content: str


class Clear(BaseModel):
    """Reset the in-memory conversation for the session."""

    type: Literal["clear"] = "clear"


class CompactRequest(BaseModel):
    """Manually trigger context compaction."""

    type: Literal["compact_request"] = "compact_request"


class ListSessions(BaseModel):
    """Ask for the list of persisted sessions."""

    type: Literal["list_sessions"] = "list_sessions"


class ModelStatus(BaseModel):
    """Query the provider/model currently bound to this session."""

    type: Literal["model_status"] = "model_status"


class ModelList(BaseModel):
    """List all configured providers and their models."""

    type: Literal["model_list"] = "model_list"


class ModelSwitch(BaseModel):
    """Switch the session's provider override by name.

    ``provider_name`` is the key from ``config.providers`` — when
    unknown the daemon returns a ``model_switch_result`` with
    ``ok=False`` and the set of available names.
    """

    type: Literal["model_switch"] = "model_switch"
    provider_name: str


class CostQuery(BaseModel):
    """Ask for the cumulative token-usage report."""

    type: Literal["cost_query"] = "cost_query"


class TasksQuery(BaseModel):
    """Ask for the current session task list."""

    type: Literal["tasks_query"] = "tasks_query"


class PlanModeRequest(BaseModel):
    """Drive the plan-mode state machine from the client.

    Attributes:
        action: ``enter`` flips the permission engine into PLAN mode,
            ``exit`` restores the previous mode, ``status`` returns a
            read-only snapshot via ``plan_mode_status``.
    """

    type: Literal["plan_mode_request"] = "plan_mode_request"
    action: Literal["enter", "exit", "status"]


class PermissionModeRequest(BaseModel):
    """Switch the session's permission mode (Step 5.8).

    Single entry point used by the CLI's Shift+Tab cycler and the
    ``--permission-mode`` startup flag.  When ``action == "plan"``
    the daemon routes through the same logic as
    :class:`PlanModeRequest` (writes the plan file, locks tools),
    so both messages stay consistent.
    """

    type: Literal["permission_mode_request"] = "permission_mode_request"
    action: Literal["default", "accept_edits", "plan", "bypass"]


class DeleteSession(BaseModel):
    """Delete a persisted session by id (cannot target the active one)."""

    type: Literal["delete_session"] = "delete_session"
    session_id: str


class UserQuestionResponseMsg(BaseModel):
    """Client's answer to a ``user_question`` event.

    Maps question text → selected label(s).
    """

    type: Literal["user_question_response"] = "user_question_response"
    request_id: str
    answers: dict[str, Any]


# ---------------------------------------------------------------------------
# Permission response — preserves legacy ``allowed`` boolean
# ---------------------------------------------------------------------------


class Interrupt(BaseModel):
    """Cancel the in-flight query (user pressed Ctrl+C)."""

    type: Literal["interrupt"] = "interrupt"


class PermissionResponseMsg(BaseModel):
    """Client's answer to a ``permission_request``.

    Accepts both the 3-way ``decision`` field (``allow`` / ``deny``
    / ``always_allow``) and the original 2-way ``allowed`` boolean
    so older CLIs keep working.  The normalized value is exposed via
    :attr:`decision`.
    """

    type: Literal["permission_response"] = "permission_response"
    request_id: str
    decision: Literal["allow", "deny", "always_allow"] = "deny"

    @model_validator(mode="before")
    @classmethod
    def _normalise_legacy_allowed(cls, data: Any) -> Any:
        """Back-compat: map legacy ``allowed: bool`` → ``decision``.

        Only engages when the caller did not already supply
        ``decision`` — the explicit 3-way value always wins.
        """
        if not isinstance(data, dict):
            return data
        if "decision" not in data and "allowed" in data:
            data = dict(data)  # don't mutate the caller's dict
            data["decision"] = "allow" if data.get("allowed") else "deny"
        return data


# ---------------------------------------------------------------------------
# Discriminated union + single parse entry point
# ---------------------------------------------------------------------------


ClientMessage = Annotated[
    UserMessage
    | PermissionResponseMsg
    | Clear
    | CompactRequest
    | Interrupt
    | ListSessions
    | ModelStatus
    | ModelList
    | ModelSwitch
    | CostQuery
    | TasksQuery
    | PlanModeRequest
    | PermissionModeRequest
    | DeleteSession
    | UserQuestionResponseMsg,
    Field(discriminator="type"),
]

# Module-level adapter so parse is O(1) per call (no rebuild).
_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_client_message(raw: dict[str, Any]) -> ClientMessage:
    """Validate and dispatch on the ``type`` field.

    Raises:
        ValidationError: raw payload does not conform to any known
            :data:`ClientMessage` variant.  The WS handler catches
            this and replies with a stringly-formed error frame.
    """
    return _adapter.validate_python(raw)


__all__ = [
    "Clear",
    "ClientMessage",
    "Interrupt",
    "CompactRequest",
    "CostQuery",
    "DeleteSession",
    "ListSessions",
    "ModelList",
    "ModelStatus",
    "ModelSwitch",
    "PermissionModeRequest",
    "PermissionResponseMsg",
    "PlanModeRequest",
    "TasksQuery",
    "UserMessage",
    "UserQuestionResponseMsg",
    "ValidationError",
    "parse_client_message",
]
