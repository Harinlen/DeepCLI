"""Permission request/response schemas for tool approval round-trips."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class PermissionRequestOption:
    """One option the client may show for a permission request.

    Options are generated dynamically by ToolAuthorizer.  Some high-risk asks
    intentionally omit ``allow_always`` so clients cannot accidentally grant a
    durable permission the policy did not offer.
    """

    option_id: str
    name: str
    kind: Literal["allow_once", "allow_always", "reject_once", "reject_always"]


@dataclass(frozen=True)
class PermissionRequest:
    """Sent to the Session layer when a tool requires user approval.

    This is a kernel-internal schema; Session maps it onto ACP
    ``session/request_permission`` and later maps the chosen option back into a
    ``PermissionResponse``.
    """

    # LLM tool_use id; every approval must be correlated to one pending call.
    tool_use_id: str
    tool_name: str
    # Human-facing summary supplied by the Tool implementation.
    tool_title: str
    # Short policy explanation from the authorizer, suitable for UI display.
    input_summary: str
    risk_level: Literal["low", "medium", "high"]
    # Optional original input so advanced clients can show editable details.
    tool_input: dict[str, Any] | None = None
    options: tuple[PermissionRequestOption, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PermissionResponse:
    """Returned by the ``PermissionCallback`` after the user decides.

    ``updated_input`` is how interactive tools such as AskUserQuestion return
    edited or free-form user data without adding a second callback channel.
    """

    decision: Literal["allow_once", "allow_always", "reject"]
    updated_input: dict[str, Any] | None = None


PermissionCallback = Callable[[PermissionRequest], Awaitable[PermissionResponse]]
"""Callback type used by ``Orchestrator.query()`` for approval prompts."""
