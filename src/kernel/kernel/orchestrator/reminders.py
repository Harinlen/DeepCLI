"""Prompt text and system-reminder helpers."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from kernel.llm.types import TextContent
from kernel.orchestrator.types import OrchestratorDeps

logger = logging.getLogger(__name__)


class ReminderPromptStore(Protocol):
    """Prompt source capable of loading system-reminder templates."""

    def get(self, key: str) -> str:
        """Return the template text for ``key``.

        Args:
            key: PromptManager key under the orchestrator prompt namespace.

        Returns:
            Template text containing a ``{reminder}`` placeholder.
        """
        ...


def extract_text(blocks: list[Any]) -> str:
    """Concatenate visible text from content blocks.

    Args:
        blocks: Raw content blocks accepted by Orchestrator query.

    Returns:
        Visible text joined with spaces.
    """
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, str):
            parts.append(block)
        else:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return " ".join(parts)


def to_text_content(
    blocks: list[Any],
    *,
    reminders: list[str] | None = None,
    prompts: ReminderPromptStore | None = None,
) -> list[TextContent]:
    """Normalise content blocks to ``list[TextContent]``.

    Args:
        blocks: Raw content blocks accepted by Orchestrator query.
        reminders: Optional system reminders prepended to user text.
        prompts: Optional prompt store used to render reminder wrappers.

    Returns:
        Non-empty list of text content blocks.
    """
    result: list[TextContent] = []
    if reminders:
        result.append(TextContent(text=format_reminders(reminders, prompts=prompts)))
    for block in blocks:
        if isinstance(block, TextContent):
            result.append(block)
        elif isinstance(block, str):
            result.append(TextContent(text=block))
        else:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                result.append(TextContent(text=text))
    return result or [TextContent(text="")]


def drain_pending_reminders(deps: OrchestratorDeps) -> list[str]:
    """Pop hook-queued system-reminder strings.

    Args:
        deps: Orchestrator dependencies containing the drain closure.

    Returns:
        Pending reminder strings, or an empty list when unavailable.
    """
    drain = deps.drain_reminders
    if drain is None:
        return []
    try:
        return list(drain())
    except Exception:
        logger.exception("drain_reminders raised - treating as empty")
        return []


def format_reminders(reminders: list[str], prompts: ReminderPromptStore | None = None) -> str:
    """Wrap reminders in ``<system-reminder>`` blocks.

    Args:
        reminders: Reminder texts queued by hooks or background tasks.
        prompts: Optional prompt store for template-driven formatting.

    Returns:
        Rendered reminder block text with trailing spacing for prompt prepend.
    """
    if prompts is not None:
        tpl = prompts.get("orchestrator/system_reminder")
        blocks = [tpl.format(reminder=reminder) for reminder in reminders]
    else:
        blocks = [f"<system-reminder>\n{reminder}\n</system-reminder>" for reminder in reminders]
    return "\n\n".join(blocks) + "\n\n"
