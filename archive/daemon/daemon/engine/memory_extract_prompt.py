"""Prompt template for the memory auto-extract sub-agent (Phase 5.7A).

Injected as the user message when the orchestrator spawns a background
sub-agent to analyse a conversation transcript and extract long-term
memories.

Prompt text lives in ``engine/prompts/memory_extract.txt``.
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

MEMORY_EXTRACT_PROMPT = (
    (_PROMPTS_DIR / "memory_extract.txt").read_text(encoding="utf-8").rstrip("\n")
)


def format_extract_prompt(transcript: str, max_new_memories: int = 3) -> str:
    """Fill the extract prompt template.

    Args:
        transcript: Formatted conversation transcript.
        max_new_memories: Cap on new memory entries per extraction.

    Returns:
        The fully formatted prompt string.
    """
    return MEMORY_EXTRACT_PROMPT.format(
        transcript=transcript,
        max_new_memories=max_new_memories,
    )
