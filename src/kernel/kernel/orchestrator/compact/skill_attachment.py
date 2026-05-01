"""Skill attachment preservation after compaction."""

from __future__ import annotations

# Skills can be long instruction files; per-skill and total caps keep
# post-compaction reminders useful without letting a skill library crowd out the
# actual conversation summary.
POST_COMPACT_MAX_TOKENS_PER_SKILL = 5_000
POST_COMPACT_SKILLS_TOKEN_BUDGET = 25_000
CHARS_PER_TOKEN = 4
SKILL_TRUNCATION_MARKER = (
    "\n\n[... skill content truncated for compaction; "
    "use Read on the skill path if you need the full text]"
)


def create_skill_attachment(
    skills: object,
    agent_id: str | None = None,
) -> str | None:
    """Build a text attachment of invoked skill bodies.

    Args:
        skills: Skill manager-like object with ``get_invoked_for_agent``.
        agent_id: Optional child-agent scope for invoked skills.

    Returns:
        XML-ish skill attachment text, or ``None`` when no invoked skills need
        preservation.

    Compaction can remove the original tool call that loaded a skill.  This
    attachment keeps the skill instructions active after history is rewritten.
    """
    if skills is None:
        return None

    try:
        get_invoked = getattr(skills, "get_invoked_for_agent")
        invoked = get_invoked(agent_id)
    except Exception:
        return None

    if not invoked:
        return None

    per_skill_char_budget = POST_COMPACT_MAX_TOKENS_PER_SKILL * CHARS_PER_TOKEN
    total_char_budget = POST_COMPACT_SKILLS_TOKEN_BUDGET * CHARS_PER_TOKEN
    sections: list[str] = []
    used_chars = 0

    for info in invoked:
        content = info.content
        if len(content) > per_skill_char_budget:
            content = content[:per_skill_char_budget] + SKILL_TRUNCATION_MARKER
        if used_chars + len(content) > total_char_budget:
            break
        # The tag shape is prompt-only, not an external XML contract.  It is kept
        # simple so the model can distinguish skill name/path/body reliably.
        sections.append(
            f'<skill name="{info.skill_name}" path="{info.skill_path}">\n{content}\n</skill>'
        )
        used_chars += len(content)

    if not sections:
        return None

    return (
        "The following skills were previously invoked in this session. "
        "Their instructions remain active:\n\n" + "\n\n".join(sections)
    )
