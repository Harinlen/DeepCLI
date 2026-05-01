"""Shared types for the Tools subsystem.

Groups the data classes exchanged between Tool implementations, the
Orchestrator's ToolExecutor, and the ToolAuthorizer.  None of these
types hold behaviour — they are plain dataclasses / unions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from kernel.protocol.interfaces.contracts.content_block import ContentBlock


# ---------------------------------------------------------------------------
# Display payload — what the client renders after a tool finishes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextDisplay:
    """Plain / markdown text block.  Bash stdout, general text output."""

    text: str
    language: str | None = None


@dataclass(frozen=True)
class DiffDisplay:
    """Before / after diff for file-editing tools."""

    path: str
    before: str | None
    after: str


@dataclass(frozen=True)
class LocationsDisplay:
    """Navigable source locations (Grep / Glob)."""

    locations: list[dict[str, Any]]
    summary: str | None = None


@dataclass(frozen=True)
class FileDisplay:
    """FileRead result — may be truncated."""

    path: str
    content: str
    truncated: bool = False


@dataclass(frozen=True)
class RawBlocks:
    """Fallback — hand ``list[ContentBlock]`` to the client unchanged."""

    blocks: list[ContentBlock]


ToolDisplayPayload = TextDisplay | DiffDisplay | LocationsDisplay | FileDisplay | RawBlocks


# ---------------------------------------------------------------------------
# Permission suggestion — Tool's view on how risky this call is
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermissionSuggestion:
    """Returned by ``Tool.default_risk``.  Authorizer consumes, arbitrates."""

    risk: Literal["low", "medium", "high"]
    default_decision: Literal["allow", "ask", "deny"]
    reason: str


# ---------------------------------------------------------------------------
# Tool execution progress / result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCallProgress:
    """Zero or more progress frames yielded while a tool runs."""

    content: list[ContentBlock]

    passthrough_event: Any = None
    """When set, ToolExecutor yields this event directly instead of
    wrapping in an orchestrator ``ToolCallProgress``.  Used by AgentTool
    to transparently forward sub-agent ``OrchestratorEvent`` instances.
    Typed as ``Any`` to avoid importing ``OrchestratorEvent`` into the
    tools layer."""


@dataclass(frozen=True)
class ToolCallResult:
    """Terminal frame yielded exactly once by a tool.

    ``data`` is structured output the Tool author returns; Orchestrator
    does **not** read it (it only touches ``llm_content`` + ``display``).
    Upper-layer Tools (AgentTool) and telemetry hooks read ``data``.
    """

    data: Any
    llm_content: list[ContentBlock]
    display: ToolDisplayPayload
    context_modifier: ContextModifier | None = None


# ``ContextModifier`` is a pure function ``ToolContext -> ToolContext``
# applied by Orchestrator after the tool finishes.  The actual callable
# shape is enforced at call-time; the alias here is a documentation anchor.
#
# Kept as ``Any`` at type-check time to avoid a circular import with
# ``kernel.tools.context``.  Callers cast via
# ``Callable[[ToolContext], ToolContext]``.
ContextModifier = Any


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ToolInputError(Exception):
    """Raised by ``Tool.validate_input`` for malformed inputs.

    Validated before permission check — the Tool rejects early without
    wasting a permission round-trip on inputs it could never execute.
    """


__all__ = [
    "ContextModifier",
    "DiffDisplay",
    "FileDisplay",
    "LocationsDisplay",
    "PermissionSuggestion",
    "RawBlocks",
    "TextDisplay",
    "ToolCallProgress",
    "ToolCallResult",
    "ToolDisplayPayload",
    "ToolInputError",
]
