"""Tests for Phase 5.5.4G — AskUserQuestion tool and QuestionHandler."""

from __future__ import annotations

import asyncio
import json

import pytest

from daemon.api.question_handler import QuestionHandler
from daemon.engine.stream import UserQuestion, UserQuestionResponse
from daemon.extensions.tools.base import ToolContext
from daemon.extensions.tools.builtin.ask_user_question import AskUserQuestionTool


# -- QuestionHandler ---------------------------------------------------------


class TestQuestionHandler:
    @pytest.mark.asyncio
    async def test_create_and_resolve(self) -> None:
        handler = QuestionHandler()
        future = handler.create_waiter("req1")
        assert handler.has_pending

        resp = UserQuestionResponse(request_id="req1", answers={"q": "a"})
        assert handler.resolve("req1", resp)
        assert (await future) == resp
        assert not handler.has_pending

    @pytest.mark.asyncio
    async def test_resolve_unknown(self) -> None:
        handler = QuestionHandler()
        resp = UserQuestionResponse(request_id="unknown", answers={})
        assert not handler.resolve("unknown", resp)

    @pytest.mark.asyncio
    async def test_cancel_all(self) -> None:
        handler = QuestionHandler()
        future = handler.create_waiter("req1")
        handler.cancel_all()
        assert future.done()
        assert future.result().answers == {}


# -- AskUserQuestionTool -----------------------------------------------------


class TestAskUserQuestionTool:
    @pytest.mark.asyncio
    async def test_no_callback(self) -> None:
        """Returns error when ask_user is not wired."""
        tool = AskUserQuestionTool()
        ctx = ToolContext(cwd="/tmp", ask_user=None)
        result = await tool.execute(
            {"questions": [{"question": "Pick one", "options": [{"label": "A"}, {"label": "B"}]}]},
            ctx,
        )
        assert result.is_error
        assert "not available" in result.output

    @pytest.mark.asyncio
    async def test_with_callback(self) -> None:
        """Successfully collects user answer via callback."""

        async def fake_ask(questions):
            return {"Pick one": "B"}

        tool = AskUserQuestionTool()
        ctx = ToolContext(cwd="/tmp", ask_user=fake_ask)
        result = await tool.execute(
            {"questions": [{"question": "Pick one", "options": [{"label": "A"}, {"label": "B"}]}]},
            ctx,
        )
        assert not result.is_error
        data = json.loads(result.output)
        assert data["Pick one"] == "B"

    @pytest.mark.asyncio
    async def test_callback_error(self) -> None:
        """Returns error when callback raises."""

        async def failing_ask(questions):
            raise RuntimeError("connection lost")

        tool = AskUserQuestionTool()
        ctx = ToolContext(cwd="/tmp", ask_user=failing_ask)
        result = await tool.execute(
            {"questions": [{"question": "Q", "options": [{"label": "A"}, {"label": "B"}]}]},
            ctx,
        )
        assert result.is_error
        assert "connection lost" in result.output

    def test_tool_metadata(self) -> None:
        tool = AskUserQuestionTool()
        assert tool.name == "ask_user_question"


# -- Stream event types ------------------------------------------------------


class TestUserQuestionEvents:
    def test_user_question_event(self) -> None:
        evt = UserQuestion(
            request_id="r1",
            questions=[{"question": "Q", "options": [{"label": "A"}]}],
        )
        assert evt.type == "user_question"

    def test_user_question_response(self) -> None:
        resp = UserQuestionResponse(request_id="r1", answers={"Q": "A"})
        assert resp.request_id == "r1"
        assert resp.answers == {"Q": "A"}
