"""System prompt constants for the memory subsystem.

``MEMORY_INSTRUCTIONS`` is injected into every system prompt (after
the static identity/tone sections) to teach the LLM how to use the
memory tools correctly.

``MEMORY_LINT_PROMPT`` is sent as a user message when the user runs
``/memory lint`` — it drives the LLM through a health-check workflow
using memory_list, file_read, memory_write, and memory_delete.

Prompt text lives in ``engine/prompts/*.txt``.
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load(name: str) -> str:
    """Read a prompt file, stripping the trailing newline."""
    return (_PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8").rstrip("\n")


MEMORY_INSTRUCTIONS = _load("memory_instructions")
MEMORY_LINT_PROMPT = _load("memory_lint")
