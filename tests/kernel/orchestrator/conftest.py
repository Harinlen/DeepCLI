"""Shared fixtures for orchestrator tests.

All tests use a ``FakeLLMProvider`` that lets each test script the
exact LLM response sequence without hitting a real API.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import pytest

from kernel.llm.types import (
    LLMChunk,
    Message,
    PromptSection,
    TextChunk,
    ThoughtChunk,
    ToolSchema,
    ToolUseChunk,
    UsageChunk,
)
from kernel.llm.config import ModelRef
from kernel.orchestrator import OrchestratorConfig, OrchestratorDeps
from kernel.orchestrator.orchestrator import StandardOrchestrator
from kernel.orchestrator.types import PermissionResponse


# ---------------------------------------------------------------------------
# Fake LLM provider
# ---------------------------------------------------------------------------


@dataclass
class FakeLLMProvider:
    """Scripted LLM provider for tests.

    Each call to ``stream()`` pops the next response from ``responses``.
    A response is a list of ``LLMChunk`` objects to yield.

    After all scripted responses are consumed, further calls raise
    ``AssertionError`` so tests catch unexpected extra LLM calls.
    """

    responses: list[list[LLMChunk]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    current_used_default: str = "fake-model"

    def model_for(self, role: str) -> str:
        if role == "default":
            return self.current_used_default
        raise KeyError(f"No model assigned for role: {role!r}")

    def add_text_response(
        self, text: str, *, input_tokens: int = 10, output_tokens: int = 5
    ) -> None:
        """Convenience: add a plain text response."""
        self.responses.append(
            [
                TextChunk(content=text),
                UsageChunk(input_tokens=input_tokens, output_tokens=output_tokens),
            ]
        )

    def add_tool_response(
        self,
        tool_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        input_tokens: int = 10,
        output_tokens: int = 5,
    ) -> None:
        """Convenience: add a tool-use response."""
        self.responses.append(
            [
                ToolUseChunk(id=tool_id, name=tool_name, input=tool_input),
                UsageChunk(input_tokens=input_tokens, output_tokens=output_tokens),
            ]
        )

    def add_thinking_response(
        self,
        thinking: str,
        signature: str,
        text: str,
        *,
        input_tokens: int = 20,
        output_tokens: int = 10,
    ) -> None:
        """Convenience: add a thinking + text response."""
        self.responses.append(
            [
                ThoughtChunk(content=thinking, signature=""),
                ThoughtChunk(content="", signature=signature),
                TextChunk(content=text),
                UsageChunk(input_tokens=input_tokens, output_tokens=output_tokens),
            ]
        )

    async def stream(
        self,
        *,
        system: list[PromptSection],
        messages: list[Message],
        tool_schemas: list[ToolSchema],
        model: str,
        temperature: float | None,
        thinking: bool = False,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMChunk, None]:
        self.calls.append(
            {
                "system": system,
                "messages": list(messages),
                "tool_schemas": tool_schemas,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if not self.responses:
            raise AssertionError("FakeLLMProvider: no more scripted responses")
        chunks = self.responses.pop(0)
        return self._emit(chunks)

    @staticmethod
    async def _emit(chunks: list[LLMChunk]) -> AsyncGenerator[LLMChunk, None]:
        for chunk in chunks:
            yield chunk


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_provider() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def make_orchestrator(fake_provider: FakeLLMProvider):
    """Factory that builds a StandardOrchestrator with the fake provider."""

    def _make(
        session_id: str = "test-session",
        config: OrchestratorConfig | None = None,
        hooks: Any = None,
        queue_reminders: Any = None,
    ) -> StandardOrchestrator:
        deps = OrchestratorDeps(
            provider=fake_provider,
            hooks=hooks,
            queue_reminders=queue_reminders,
        )
        return StandardOrchestrator(
            deps=deps,
            session_id=session_id,
            config=config
            or OrchestratorConfig(
                model=ModelRef(provider="fake", model="fake-model"),
                temperature=None,
            ),
        )

    return _make


async def no_permission(req: Any) -> PermissionResponse:
    """Permission callback that always rejects (should not be called in most tests)."""
    raise AssertionError(f"Unexpected permission request for tool '{req.tool_name}'")


async def allow_once(req: Any) -> PermissionResponse:
    """Permission callback that always allows once."""
    return PermissionResponse(decision="allow_once")
