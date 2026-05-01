"""BashClassifier — LLMJudge integration with a fake LLMManager stream."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest

from kernel.llm.types import StreamError, TextChunk
from kernel.tool_authz.bash_classifier import (
    MAX_CONSECUTIVE,
    BashClassifier,
)


class FakeLLMManager:
    """Mimics the LLMManager.stream contract used by the classifier."""

    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks
        self.calls: list[dict[str, Any]] = []

    async def stream(self, **kwargs: Any) -> AsyncGenerator[Any, None]:
        self.calls.append(kwargs)
        for chunk in self._chunks:
            yield chunk


@pytest.mark.anyio
async def test_llm_safe_verdict_returns_safe() -> None:
    llm = FakeLLMManager([TextChunk(content='{"verdict": "safe", "reason": "read-only"}')])
    classifier = BashClassifier(enabled=True)
    classifier.on_session_open("s")
    verdict = await classifier.classify(
        session_id="s",
        command="ls -la",
        cwd="/home/user/project",
        llm_manager=llm,
        model_ref="haiku",
    )
    assert verdict == "safe"
    # One LLM call; temperature fixed at 0 for deterministic classification.
    assert len(llm.calls) == 1
    assert llm.calls[0]["temperature"] == 0.0
    assert llm.calls[0]["model"] == "haiku"


@pytest.mark.anyio
async def test_llm_unsafe_verdict_returns_unsafe_and_registers_denial() -> None:
    llm = FakeLLMManager([TextChunk(content='{"verdict": "unsafe", "reason": "rm"}')])
    classifier = BashClassifier(enabled=True)
    classifier.on_session_open("s")

    verdict = await classifier.classify(
        session_id="s",
        command="rm something",
        cwd="/",
        llm_manager=llm,
        model_ref="haiku",
    )
    assert verdict == "unsafe"
    assert classifier._counters["s"].consecutive == 1
    assert classifier._counters["s"].total == 1


@pytest.mark.anyio
async def test_llm_consecutive_denials_trip_budget() -> None:
    """MAX_CONSECUTIVE unsafe verdicts in a row → 'budget_exceeded'."""
    llm = FakeLLMManager([TextChunk(content='{"verdict": "unsafe", "reason": "x"}')])
    classifier = BashClassifier(enabled=True)
    classifier.on_session_open("s")

    # First N calls return unsafe and register against the budget.
    for _ in range(MAX_CONSECUTIVE):
        verdict = await classifier.classify(
            session_id="s",
            command="dangerous cmd",
            cwd="/",
            llm_manager=llm,
            model_ref="haiku",
        )
        assert verdict == "unsafe"
        # Reset fixture so next iteration sees chunks again.
        llm._chunks = [TextChunk(content='{"verdict": "unsafe", "reason": "x"}')]

    # Next call short-circuits to budget_exceeded without hitting the LLM.
    verdict = await classifier.classify(
        session_id="s",
        command="another cmd",
        cwd="/",
        llm_manager=llm,
        model_ref="haiku",
    )
    assert verdict == "budget_exceeded"
    assert len(llm.calls) == MAX_CONSECUTIVE  # no extra call after budget exhausted


@pytest.mark.anyio
async def test_llm_safe_verdict_resets_consecutive_but_not_total() -> None:
    classifier = BashClassifier(enabled=True)
    classifier.on_session_open("s")

    unsafe_llm = FakeLLMManager([TextChunk(content='{"verdict": "unsafe", "reason": "x"}')])
    safe_llm = FakeLLMManager([TextChunk(content='{"verdict": "safe", "reason": "ok"}')])

    await classifier.classify(
        session_id="s", command="rm x", cwd="/", llm_manager=unsafe_llm, model_ref="h"
    )
    await classifier.classify(
        session_id="s", command="ls", cwd="/", llm_manager=safe_llm, model_ref="h"
    )

    counters = classifier._counters["s"]
    assert counters.consecutive == 0
    assert counters.total == 1


@pytest.mark.anyio
async def test_llm_unparseable_response_returns_unknown() -> None:
    llm = FakeLLMManager([TextChunk(content="random non-JSON junk")])
    classifier = BashClassifier(enabled=True)
    classifier.on_session_open("s")
    verdict = await classifier.classify(
        session_id="s",
        command="ls",
        cwd="/",
        llm_manager=llm,
        model_ref="haiku",
    )
    assert verdict == "unknown"


@pytest.mark.anyio
async def test_llm_fenced_json_is_unwrapped() -> None:
    """Models sometimes wrap JSON in markdown fences; the parser tolerates it."""
    llm = FakeLLMManager([TextChunk(content='```json\n{"verdict": "safe", "reason": "ok"}\n```')])
    classifier = BashClassifier(enabled=True)
    classifier.on_session_open("s")
    verdict = await classifier.classify(
        session_id="s",
        command="ls",
        cwd="/",
        llm_manager=llm,
        model_ref="h",
    )
    assert verdict == "safe"


@pytest.mark.anyio
async def test_llm_stream_error_fail_closed_returns_unsafe() -> None:
    llm = FakeLLMManager([StreamError(message="rate limited", code="429")])
    classifier = BashClassifier(enabled=True, fail_closed=True)
    classifier.on_session_open("s")
    verdict = await classifier.classify(
        session_id="s",
        command="ls",
        cwd="/",
        llm_manager=llm,
        model_ref="h",
    )
    assert verdict == "unsafe"
    assert classifier._counters["s"].total == 1


@pytest.mark.anyio
async def test_llm_stream_error_fail_open_returns_unknown() -> None:
    llm = FakeLLMManager([StreamError(message="boom", code="500")])
    classifier = BashClassifier(enabled=True, fail_closed=False)
    classifier.on_session_open("s")
    verdict = await classifier.classify(
        session_id="s",
        command="ls",
        cwd="/",
        llm_manager=llm,
        model_ref="h",
    )
    assert verdict == "unknown"
    # fail-open doesn't charge the denial counter.
    assert classifier._counters["s"].total == 0
