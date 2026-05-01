"""Static system-prompt sections — identical across every session.

Prompt text lives in ``engine/prompts/*.txt`` so it can be edited
without touching Python code.  This module loads the files once at
import time and re-exports the joined ``STATIC_PROMPT`` string plus
the plan-mode constants.

All names are re-exported from ``context.py`` for backward
compatibility.
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load(name: str) -> str:
    """Read a prompt file, stripping the trailing newline."""
    return (_PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8").rstrip("\n")


# -- Static sections (cacheable, identical across sessions) ----------------

_IDENTITY = _load("identity")
_SYSTEM = _load("system")
_DOING_TASKS = _load("doing_tasks")
_ACTIONS_WITH_CARE = _load("actions_with_care")
_USING_TOOLS = _load("using_tools")
_TONE = _load("tone")
_OUTPUT_EFFICIENCY = _load("output_efficiency")
_GIT_SAFETY = _load("git_safety").replace(
    "{git_operations_path}", str(_PROMPTS_DIR / "git_operations.txt")
)

STATIC_PROMPT = "\n\n".join(
    [
        _IDENTITY,
        _SYSTEM,
        _DOING_TASKS,
        _ACTIONS_WITH_CARE,
        _USING_TOOLS,
        _GIT_SAFETY,
        _TONE,
        _OUTPUT_EFFICIENCY,
    ]
)
"""All static sections joined — identical across sessions."""


# -- Plan mode prompts -----------------------------------------------------

PLAN_MODE_INSTRUCTIONS = _load("plan_mode")
PLAN_MODE_REMINDER = _load("plan_mode_reminder")


__all__ = [
    "PLAN_MODE_INSTRUCTIONS",
    "PLAN_MODE_REMINDER",
    "STATIC_PROMPT",
]
