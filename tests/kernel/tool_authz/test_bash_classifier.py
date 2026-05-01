"""BashClassifier — denial tracking + budget behavior."""

from __future__ import annotations

import pytest

from kernel.tool_authz.bash_classifier import (
    MAX_CONSECUTIVE,
    MAX_TOTAL,
    BashClassifier,
    DenialCounters,
)


def test_denial_counters_consecutive_and_total() -> None:
    counters = DenialCounters()
    assert not counters.budget_exceeded()

    for _ in range(MAX_CONSECUTIVE):
        counters.register_unsafe()
    assert counters.budget_exceeded()


def test_safe_resets_consecutive_not_total() -> None:
    counters = DenialCounters()
    for _ in range(MAX_CONSECUTIVE - 1):
        counters.register_unsafe()
    counters.register_safe()
    assert counters.consecutive == 0
    assert counters.total == MAX_CONSECUTIVE - 1


def test_total_budget_trips_regardless_of_consecutive() -> None:
    counters = DenialCounters()
    for _ in range(MAX_TOTAL):
        counters.register_unsafe()
        counters.register_safe()  # reset consecutive each time
    assert counters.total == MAX_TOTAL
    assert counters.budget_exceeded()


@pytest.mark.anyio
async def test_classify_returns_unknown_without_llm() -> None:
    """No llm_manager configured → 'unknown' (user gets prompted)."""
    c = BashClassifier(enabled=True)
    c.on_session_open("s-1")
    verdict = await c.classify(
        session_id="s-1",
        command="ls",
        cwd="/",
        llm_manager=None,
        model_ref=None,
    )
    assert verdict == "unknown"


@pytest.mark.anyio
async def test_classify_respects_disabled_flag() -> None:
    c = BashClassifier(enabled=False)
    c.on_session_open("s-1")
    verdict = await c.classify(
        session_id="s-1",
        command="ls",
        cwd="/",
        llm_manager=object(),  # non-None but irrelevant when disabled
        model_ref="haiku",
    )
    assert verdict == "unknown"


@pytest.mark.anyio
async def test_classify_returns_budget_exceeded_once_tripped() -> None:
    c = BashClassifier(enabled=True)
    c.on_session_open("s-1")

    counters = c._counters["s-1"]
    for _ in range(MAX_TOTAL):
        counters.register_unsafe()

    verdict = await c.classify(
        session_id="s-1",
        command="ls",
        cwd="/",
        llm_manager=None,
        model_ref=None,
    )
    assert verdict == "budget_exceeded"
