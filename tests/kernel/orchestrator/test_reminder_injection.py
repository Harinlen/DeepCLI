"""Orchestrator — drain pending reminders at turn start + inject into prompt."""

from __future__ import annotations

from kernel.orchestrator.orchestrator import (
    _drain_pending_reminders,
    _format_reminders,
    _to_text_content,
)
from kernel.orchestrator.types import OrchestratorDeps


def test_format_reminders_wraps_in_system_reminder_tags() -> None:
    text = _format_reminders(["one", "two"])
    assert "<system-reminder>\none\n</system-reminder>" in text
    assert "<system-reminder>\ntwo\n</system-reminder>" in text
    # Ends with a double newline so the user prompt starts on its own line.
    assert text.endswith("\n\n")


def test_drain_pending_reminders_returns_empty_when_callback_missing() -> None:
    deps = OrchestratorDeps(provider=None)  # type: ignore[arg-type]
    assert _drain_pending_reminders(deps) == []


def test_drain_pending_reminders_invokes_callback() -> None:
    pending = ["first", "second"]

    def _drain() -> list[str]:
        out = list(pending)
        pending.clear()
        return out

    deps = OrchestratorDeps(provider=None, drain_reminders=_drain)  # type: ignore[arg-type]
    assert _drain_pending_reminders(deps) == ["first", "second"]
    # Callback mutated its own state — second call returns empty.
    assert _drain_pending_reminders(deps) == []


def test_drain_pending_reminders_swallows_exception() -> None:
    def _boom() -> list[str]:
        raise RuntimeError("drain blew up")

    deps = OrchestratorDeps(provider=None, drain_reminders=_boom)  # type: ignore[arg-type]
    # Must not propagate; returns empty + logs.
    assert _drain_pending_reminders(deps) == []


def test_to_text_content_prepends_reminders() -> None:
    from kernel.llm.types import TextContent

    blocks = [TextContent(text="user question")]
    out = _to_text_content(blocks, reminders=["remember X"])
    # Leading TextContent is the reminder block.
    assert len(out) == 2
    assert "remember X" in out[0].text
    assert "<system-reminder>" in out[0].text
    assert out[1].text == "user question"


def test_to_text_content_without_reminders_is_pass_through() -> None:
    from kernel.llm.types import TextContent

    blocks = [TextContent(text="just a question")]
    out = _to_text_content(blocks)
    assert len(out) == 1
    assert out[0].text == "just a question"
