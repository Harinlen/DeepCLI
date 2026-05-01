"""Pydantic schemas + enums for the memory store.

``MemoryFrontmatter`` models the YAML frontmatter at the top of each
memory ``.md`` file; ``MemoryRecord`` is the in-memory representation
returned by :meth:`MemoryStore.records`.

The semantic edges (description = compressed fact, type = cross-
project category) are enforced by the system prompt instructions and
by the ``/memory lint`` prompt — not by schema validation.  See
``MEMORY_INSTRUCTIONS`` in ``engine/memory_prompt.py``.
"""

from __future__ import annotations

import enum
from pathlib import Path

from pydantic import BaseModel, Field


class MemoryScope(str, enum.Enum):
    """Scope of a memory store — global (cross-project) or project-local."""

    GLOBAL = "global"
    """Cross-project long-term memory under ``~/.mustang/memory/``."""

    PROJECT = "project"
    """Project-local short-term memory under ``.mustang/memory/``."""


class MemoryType(str, enum.Enum):
    """Memory categories.

    Global scope uses: USER, FEEDBACK, PROJECT, REFERENCE.
    Project scope uses: TASK, CONTEXT.
    """

    # -- Global types ---------------------------------------------------
    USER = "user"
    """User identity, role, preferences."""

    FEEDBACK = "feedback"
    """Corrections/confirmations that apply across projects."""

    PROJECT = "project"
    """Cross-project initiatives, principles, conventions."""

    REFERENCE = "reference"
    """Pointers to external systems (dashboards, docs, channels)."""

    # -- Project types (Phase 5.7C) ------------------------------------
    TASK = "task"
    """Current in-progress tasks, TODOs (project-local)."""

    CONTEXT = "context"
    """Project-specific architecture, API quirks (project-local)."""


# Type whitelists per scope.
GLOBAL_TYPES: frozenset[MemoryType] = frozenset(
    {
        MemoryType.USER,
        MemoryType.FEEDBACK,
        MemoryType.PROJECT,
        MemoryType.REFERENCE,
    }
)
PROJECT_TYPES: frozenset[MemoryType] = frozenset(
    {
        MemoryType.TASK,
        MemoryType.CONTEXT,
    }
)


class MemoryKind(str, enum.Enum):
    """Shape of a memory file's body."""

    STANDALONE = "standalone"
    """Multi-paragraph fact with Why/How sections — one claim per file."""

    AGGREGATE = "aggregate"
    """Bullet list of small related facts grouped into ``##`` sections."""


class MemoryFrontmatter(BaseModel):
    """YAML frontmatter of a memory file.

    The ``description`` field is the single most important one: it
    appears verbatim in the memory index that gets injected into every
    system prompt, so it must carry the actual fact, not a category
    label.  SkillRouter paper: cross-encoder attention on skill body
    was 91.7%, on description 1.0% — so the signal has to be in the
    description itself at index-time.
    """

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)
    type: MemoryType
    kind: MemoryKind = MemoryKind.STANDALONE

    model_config = {"extra": "forbid"}


class MemoryRecord(BaseModel):
    """In-memory representation of a memory file.

    Used for ``MemoryStore.records()`` results and ``memory_list``
    tool output.  Path fields are always absolute / POSIX-normalised
    for JSON round-tripping.
    """

    relative: str
    """POSIX-style path relative to the memory root, e.g. ``user/role.md``."""

    path: Path
    """Absolute on-disk path."""

    frontmatter: MemoryFrontmatter

    size_bytes: int

    model_config = {"arbitrary_types_allowed": True}
