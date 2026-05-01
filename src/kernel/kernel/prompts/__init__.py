"""Prompt management — centralised loading and rendering of prompt text.

PromptManager is a bootstrap service (like FlagManager / ConfigManager):
it loads all ``.txt`` prompt files from ``default/`` at startup and
serves them by key at runtime.  No file I/O after ``load()``.

See ``docs/plans/landed/prompt-manager.md`` for the design doc and
``docs/architecture/decisions.md`` D18 for the governing decision.
"""

from kernel.prompts.manager import PromptKeyError, PromptLoadError, PromptManager

__all__ = [
    "PromptKeyError",
    "PromptLoadError",
    "PromptManager",
]
