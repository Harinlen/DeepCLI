"""Shared types and constants for tool execution."""

from __future__ import annotations

from typing import TypeAlias

from kernel.llm.types import ToolResultContent
from kernel.orchestrator.events import OrchestratorEvent

# Matches the intent of Claude Code's "parallel safe reads" without letting one
# model response spawn unbounded kernel tasks.
DEFAULT_MAX_CONCURRENCY = 10

# Skill discovery currently needs notifications only for tools that can create
# or modify files.  Delete/move tools can be added when they expose enough path
# metadata for reliable invalidation.
FILE_MUTATING_TOOLS = frozenset({"FileEdit", "FileWrite"})

# Queue payloads are always EventPair, so ``None`` is reserved as end-of-stream.
SENTINEL = None

EventPair: TypeAlias = tuple[OrchestratorEvent, ToolResultContent | None]
"""One client event plus the optional LLM-facing tool result to append."""
