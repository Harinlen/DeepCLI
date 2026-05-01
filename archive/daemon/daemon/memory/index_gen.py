"""Generate ``index.md`` from the current MemoryStore record set.

The index is a flat, by-type-sectioned markdown file whose lines are
copies of each memory's frontmatter description — chosen so the LLM
can decide relevance from the system prompt alone without calling
``file_read`` on every entry (SkillRouter finding).

Format::

    # Memory Index

    ## User
    - [role.md](user/role.md) — TrueNorth backend engineer, deep Go…
    - [preferences.md](user/preferences.md) — prefers pytest, tabs, …

    ## Feedback
    ...

See ``docs/plans/pending/phase4-batch3.md`` §"index.md 生成规则".
"""

from __future__ import annotations

from daemon.memory.schema import MemoryRecord, MemoryType

MAX_INDEX_LINES = 200

# Fixed section order — mirrors MemoryType declaration order.
# Includes both global and project types (Phase 5.7C).
_TYPE_ORDER: tuple[MemoryType, ...] = (
    MemoryType.USER,
    MemoryType.FEEDBACK,
    MemoryType.PROJECT,
    MemoryType.REFERENCE,
    MemoryType.TASK,
    MemoryType.CONTEXT,
)

_TYPE_HEADINGS: dict[MemoryType, str] = {
    MemoryType.USER: "User",
    MemoryType.FEEDBACK: "Feedback",
    MemoryType.PROJECT: "Project",
    MemoryType.REFERENCE: "Reference",
    MemoryType.TASK: "Task",
    MemoryType.CONTEXT: "Context",
}


def render_index(records: list[MemoryRecord]) -> str:
    """Build the index.md text from a record list.

    Records are grouped by type (fixed order) and sorted within each
    group by frontmatter.name.  Empty sections are omitted entirely.
    Output is truncated to ``MAX_INDEX_LINES`` with a trailing hint
    when it overflows.
    """
    by_type: dict[MemoryType, list[MemoryRecord]] = {t: [] for t in _TYPE_ORDER}
    for rec in records:
        by_type[rec.frontmatter.type].append(rec)
    for t in by_type:
        by_type[t].sort(key=lambda r: r.frontmatter.name.lower())

    lines: list[str] = ["# Memory Index", ""]
    for t in _TYPE_ORDER:
        bucket = by_type[t]
        if not bucket:
            continue
        lines.append(f"## {_TYPE_HEADINGS[t]}")
        for rec in bucket:
            # filename link relative to memory root
            lines.append(
                f"- [{_filename(rec.relative)}]({rec.relative}) — {rec.frontmatter.description}"
            )
        lines.append("")  # blank line between sections

    # Strip trailing blank line if present
    while lines and lines[-1] == "":
        lines.pop()

    # Truncation guard
    if len(lines) > MAX_INDEX_LINES:
        kept = lines[:MAX_INDEX_LINES]
        overflow = len(lines) - MAX_INDEX_LINES
        kept.append(f"\n...({overflow} more entries, use memory_list for full listing)")
        lines = kept

    return "\n".join(lines) + "\n"


def _filename(relative: str) -> str:
    """Return just the filename from a ``type/name.md`` relative path."""
    _, _, tail = relative.partition("/")
    return tail or relative
