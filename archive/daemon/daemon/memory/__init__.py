"""Cross-project long-term memory subsystem.

The memory store lives at ``~/.mustang/memory/`` and holds user
identity, preferences, feedback, and external references that apply
across projects and sessions.  Content is managed by the LLM via
the memory tools (``memory_write`` / ``memory_append`` /
``memory_delete`` / ``memory_list``); MemoryStore is the sole writer
to the directory.

See ``docs/plans/pending/phase4-batch3.md`` and decision D17 for the
full design.
"""

from daemon.memory.log import MemoryLog
from daemon.memory.schema import (
    MemoryFrontmatter,
    MemoryKind,
    MemoryRecord,
    MemoryType,
)
from daemon.memory.store import DEFAULT_MEMORY_ROOT, MemoryStore

__all__ = [
    "DEFAULT_MEMORY_ROOT",
    "MemoryFrontmatter",
    "MemoryKind",
    "MemoryLog",
    "MemoryRecord",
    "MemoryStore",
    "MemoryType",
]
