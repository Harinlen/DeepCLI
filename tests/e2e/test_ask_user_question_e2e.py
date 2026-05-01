"""E2E tests for AskUserQuestion tool.

Exercises AskUserQuestion through the real ACP WebSocket interface.
A live kernel must be running (started by the ``kernel`` session fixture
in ``conftest.py``).

Coverage map
------------
test_ask_user_question_happy_path  → Full round-trip: LLM calls AskUserQuestion,
                                      client answers via permission channel,
                                      LLM receives formatted answers.
test_ask_user_question_reject      → Client rejects the permission request,
                                      LLM receives denial message.
test_ask_user_question_text_path   → LLM calls a text question, client returns
                                      a free-form answer via updated_input.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ToolCallEvent,
    TurnComplete,
)


# Timeout for non-LLM operations.
_TEST_TIMEOUT: float = 30.0
# Timeout for tests that include LLM round-trips.
_LLM_TIMEOUT: float = 120.0


def _run(coro: Any, *, timeout: float = _TEST_TIMEOUT) -> Any:
    """Run an async coroutine with a hard timeout to prevent hangs."""

    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    return asyncio.run(_guarded())


def _client(
    port: int,
    token: str,
    *,
    request_timeout: float = _TEST_TIMEOUT,
) -> Any:
    """Create a ProbeClient with the e2e request timeout."""
    from probe.client import ProbeClient

    return ProbeClient(port=port, token=token, request_timeout=request_timeout)


async def _list_profiles(port: int, token: str) -> list[dict[str, Any]]:
    async with _client(port, token) as client:
        await client.initialize()
        result = await client._request("_mustang.agent/model/profile_list", {})
    return result.get("profiles", [])


def _skip_if_no_llm(port: int, token: str) -> None:
    profiles = _run(_list_profiles(port, token))
    if not profiles:
        pytest.skip("No LLM model profiles configured — skipping")


# ---------------------------------------------------------------------------
# 1. Happy path: LLM calls AskUserQuestion, client answers, LLM continues
# ---------------------------------------------------------------------------


def test_ask_user_question_happy_path(kernel: tuple[int, str]) -> None:
    """Full round-trip through the real kernel:

    1. Prompt instructs LLM to call AskUserQuestion.
    2. Kernel sends ``session/request_permission`` with tool_input
       containing the questions.
    3. Probe client replies with ``updated_input`` carrying answers.
    4. LLM receives the formatted answers and continues.

    Verifies: ToolSearch promotion, permission round-trip with
    updated_input, answer formatting in tool result.
    """
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _run_prompt() -> tuple[str, list[str], str]:
        text_parts: list[str] = []
        tool_titles: list[str] = []
        stop_reason = "unknown"
        async with _client(port, token, request_timeout=_LLM_TIMEOUT) as client:
            await client.initialize()
            sid = await client.new_session()

            prompt = (
                'First, use ToolSearch with query "select:AskUserQuestion" to load the tool. '
                "Then call AskUserQuestion with exactly one question: "
                '"Which framework do you prefer?" with header "Framework" '
                "and two options: "
                '{"label": "React", "description": "A JavaScript library"} and '
                '{"label": "Vue", "description": "A progressive framework"}. '
                "After getting the answer, repeat the user's answer verbatim "
                "in your response."
            )
            async for event in client.prompt(sid, prompt):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_titles.append(event.title)
                elif isinstance(event, PermissionRequest):
                    # Check if this is the AskUserQuestion permission
                    if event.tool_input is not None and "questions" in event.tool_input:
                        # Answer the question via updated_input
                        await client.reply_permission(
                            event.req_id,
                            "allow_once",
                            updated_input={
                                "questions": event.tool_input["questions"],
                                "answers": {
                                    "Which framework do you prefer?": "React",
                                },
                            },
                        )
                    else:
                        # Regular permission request (e.g. ToolSearch)
                        await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return "".join(text_parts), tool_titles, stop_reason

    text, tool_titles, stop_reason = _run(_run_prompt(), timeout=_LLM_TIMEOUT)

    assert stop_reason == "end_turn"
    # The LLM should mention "React" in its response (the answer we provided).
    assert "React" in text, f"Expected 'React' in response. Got: {text[:500]}"


# ---------------------------------------------------------------------------
# 2. Reject path: client rejects the permission, tool call is denied
# ---------------------------------------------------------------------------


def test_ask_user_question_reject(kernel: tuple[int, str]) -> None:
    """When the client rejects the permission request, the tool call
    should fail with a denial message, and the LLM should continue.
    """
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _run_prompt() -> tuple[str, str]:
        text_parts: list[str] = []
        stop_reason = "unknown"
        async with _client(port, token, request_timeout=_LLM_TIMEOUT) as client:
            await client.initialize()
            sid = await client.new_session()

            prompt = (
                'First, use ToolSearch with query "select:AskUserQuestion" to load the tool. '
                "Then call AskUserQuestion with one question: "
                '"Pick a color?" with header "Color" '
                "and options: "
                '{"label": "Red", "description": "Warm"} and '
                '{"label": "Blue", "description": "Cool"}. '
                "If the tool call fails or is rejected, say exactly: REJECTED"
            )
            async for event in client.prompt(sid, prompt):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, PermissionRequest):
                    if event.tool_input is not None and "questions" in event.tool_input:
                        # Reject the question
                        await client.reply_permission(event.req_id, "reject")
                    else:
                        await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return "".join(text_parts), stop_reason

    text, stop_reason = _run(_run_prompt(), timeout=_LLM_TIMEOUT)

    assert stop_reason == "end_turn"
    # The LLM should acknowledge the rejection.
    assert "REJECTED" in text.upper() or "reject" in text.lower() or "denied" in text.lower(), (
        f"Expected rejection acknowledgement in response. Got: {text[:500]}"
    )


# ---------------------------------------------------------------------------
# 3. Text path: client returns a free-form answer through updated_input
# ---------------------------------------------------------------------------


def test_ask_user_question_text_path(kernel: tuple[int, str]) -> None:
    """Text questions do not need options and still use updated_input answers."""
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _run_prompt() -> tuple[str, str, bool]:
        text_parts: list[str] = []
        stop_reason = "unknown"
        saw_text_question = False
        async with _client(port, token, request_timeout=_LLM_TIMEOUT) as client:
            await client.initialize()
            sid = await client.new_session()

            prompt = (
                'First, use ToolSearch with query "select:AskUserQuestion" to load the tool. '
                "Then call AskUserQuestion with exactly one text question, not a choice question: "
                '{"type": "text", "question": "What should the project name be?", '
                '"header": "Name", "placeholder": "Project name", "maxLength": 80}. '
                "Do not include options for this question. "
                "After getting the answer, repeat the project name verbatim."
            )
            async for event in client.prompt(sid, prompt):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, PermissionRequest):
                    if event.tool_input is not None and "questions" in event.tool_input:
                        questions = event.tool_input["questions"]
                        saw_text_question = any(
                            isinstance(q, dict) and q.get("type") == "text" for q in questions
                        )
                        await client.reply_permission(
                            event.req_id,
                            "allow_once",
                            updated_input={
                                "questions": questions,
                                "answers": {
                                    "What should the project name be?": "Mustang Studio",
                                },
                            },
                        )
                    else:
                        await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return "".join(text_parts), stop_reason, saw_text_question

    text, stop_reason, saw_text_question = _run(_run_prompt(), timeout=_LLM_TIMEOUT)

    assert stop_reason == "end_turn"
    assert saw_text_question
    assert "Mustang Studio" in text, f"Expected project name in response. Got: {text[:500]}"
