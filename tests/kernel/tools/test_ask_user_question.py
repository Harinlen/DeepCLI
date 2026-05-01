"""AskUserQuestionTool — unit tests.

Tests cover:
- Tool metadata and schema
- Input validation (happy + error paths)
- default_risk returns "ask"
- call() formats answers correctly
- call() handles missing answers gracefully
- call() includes annotation notes
- Permission round-trip integration (updated_input forwarding)
- Registry deferred behaviour
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kernel.tools.builtin.ask_user_question import AskUserQuestionTool
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache
from kernel.tools.registry import ToolRegistry
from kernel.tools.types import ToolCallResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx() -> ToolContext:
    return ToolContext(
        session_id="test",
        agent_depth=0,
        agent_id=None,
        cwd=Path.cwd(),
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
    )


def _make_questions(n: int = 1) -> list[dict[str, Any]]:
    """Build n valid question dicts."""
    questions = []
    for i in range(n):
        questions.append(
            {
                "question": f"Question {i}?",
                "header": f"Q{i}",
                "options": [
                    {"label": f"Option A{i}", "description": f"Desc A{i}"},
                    {"label": f"Option B{i}", "description": f"Desc B{i}"},
                ],
            }
        )
    return questions


def _make_text_question(
    *,
    question: str = "What should the project name be?",
    header: str = "Name",
) -> dict[str, Any]:
    """Build a valid free-form text question."""
    return {
        "type": "text",
        "question": question,
        "header": header,
        "placeholder": "Project name",
        "maxLength": 80,
    }


def _make_input(
    *,
    questions: list[dict[str, Any]] | None = None,
    answers: dict[str, str] | None = None,
    annotations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"questions": questions or _make_questions()}
    if answers is not None:
        result["answers"] = answers
    if annotations is not None:
        result["annotations"] = annotations
    return result


async def _run(tool: AskUserQuestionTool, input: dict[str, Any]) -> ToolCallResult:
    ctx = _make_ctx()
    result = None
    async for event in tool.call(input, ctx):
        if isinstance(event, ToolCallResult):
            result = event
    assert result is not None
    return result


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_name(self) -> None:
        t = AskUserQuestionTool()
        assert t.name == "AskUserQuestion"

    def test_deferred(self) -> None:
        assert AskUserQuestionTool.should_defer is True

    def test_not_destructive(self) -> None:
        t = AskUserQuestionTool()
        assert t.is_destructive({}) is False

    def test_kind_is_other(self) -> None:
        from kernel.orchestrator.types import ToolKind

        assert AskUserQuestionTool.kind == ToolKind.other

    def test_user_facing_name(self) -> None:
        t = AskUserQuestionTool()
        assert t.user_facing_name({}) == "Ask user"

    def test_activity_description(self) -> None:
        t = AskUserQuestionTool()
        assert t.activity_description({}) == "Asking the user a question"

    def test_schema_has_questions(self) -> None:
        t = AskUserQuestionTool()
        schema = t.to_schema()
        assert "questions" in schema.input_schema["properties"]

    def test_schema_allows_choice_and_text_questions(self) -> None:
        t = AskUserQuestionTool()
        items = t.to_schema().input_schema["properties"]["questions"]["items"]
        assert "anyOf" in items
        question_types = {
            variant["properties"].get("type", {}).get("enum", [None])[0]
            for variant in items["anyOf"]
        }
        assert question_types == {"choice", "text"}

    def test_concurrency_safe(self) -> None:
        t = AskUserQuestionTool()
        assert t.is_concurrency_safe is True


# ---------------------------------------------------------------------------
# default_risk
# ---------------------------------------------------------------------------


class TestDefaultRisk:
    def test_returns_ask(self) -> None:
        t = AskUserQuestionTool()
        suggestion = t.default_risk(_make_input(), _make_ctx())
        assert suggestion.default_decision == "ask"
        assert suggestion.risk == "low"


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------


class TestValidateInput:
    @pytest.mark.asyncio
    async def test_valid_input(self) -> None:
        t = AskUserQuestionTool()
        # Should not raise.
        await t.validate_input(_make_input(), _make_ctx())

    @pytest.mark.asyncio
    async def test_valid_text_question_without_options(self) -> None:
        t = AskUserQuestionTool()
        await t.validate_input({"questions": [_make_text_question()]}, _make_ctx())

    @pytest.mark.asyncio
    async def test_missing_questions(self) -> None:
        from kernel.tools.types import ToolInputError

        t = AskUserQuestionTool()
        with pytest.raises(ToolInputError, match="questions"):
            await t.validate_input({}, _make_ctx())

    @pytest.mark.asyncio
    async def test_empty_questions(self) -> None:
        from kernel.tools.types import ToolInputError

        t = AskUserQuestionTool()
        with pytest.raises(ToolInputError, match="questions"):
            await t.validate_input({"questions": []}, _make_ctx())

    @pytest.mark.asyncio
    async def test_too_many_questions(self) -> None:
        from kernel.tools.types import ToolInputError

        t = AskUserQuestionTool()
        with pytest.raises(ToolInputError, match="at most 4"):
            await t.validate_input({"questions": _make_questions(5)}, _make_ctx())

    @pytest.mark.asyncio
    async def test_duplicate_question_text(self) -> None:
        from kernel.tools.types import ToolInputError

        t = AskUserQuestionTool()
        qs = _make_questions(2)
        qs[1]["question"] = qs[0]["question"]
        with pytest.raises(ToolInputError, match="duplicate question"):
            await t.validate_input({"questions": qs}, _make_ctx())

    @pytest.mark.asyncio
    async def test_too_few_options(self) -> None:
        from kernel.tools.types import ToolInputError

        t = AskUserQuestionTool()
        qs = _make_questions(1)
        qs[0]["options"] = [{"label": "Only one", "description": "desc"}]
        with pytest.raises(ToolInputError, match="at least 2"):
            await t.validate_input({"questions": qs}, _make_ctx())

    @pytest.mark.asyncio
    async def test_choice_question_missing_options_still_fails(self) -> None:
        from kernel.tools.types import ToolInputError

        t = AskUserQuestionTool()
        qs = [{"type": "choice", "question": "Pick one?", "header": "Pick"}]
        with pytest.raises(ToolInputError, match="options"):
            await t.validate_input({"questions": qs}, _make_ctx())

    @pytest.mark.asyncio
    async def test_too_many_options(self) -> None:
        from kernel.tools.types import ToolInputError

        t = AskUserQuestionTool()
        qs = _make_questions(1)
        qs[0]["options"] = [{"label": f"Opt {i}", "description": f"Desc {i}"} for i in range(5)]
        with pytest.raises(ToolInputError, match="at most 4"):
            await t.validate_input({"questions": qs}, _make_ctx())

    @pytest.mark.asyncio
    async def test_duplicate_option_label(self) -> None:
        from kernel.tools.types import ToolInputError

        t = AskUserQuestionTool()
        qs = _make_questions(1)
        qs[0]["options"][1]["label"] = qs[0]["options"][0]["label"]
        with pytest.raises(ToolInputError, match="duplicate option label"):
            await t.validate_input({"questions": qs}, _make_ctx())

    @pytest.mark.asyncio
    async def test_missing_question_text(self) -> None:
        from kernel.tools.types import ToolInputError

        t = AskUserQuestionTool()
        qs = [
            {
                "header": "H",
                "options": [
                    {"label": "A", "description": "a"},
                    {"label": "B", "description": "b"},
                ],
            }
        ]
        with pytest.raises(ToolInputError, match="question.*required"):
            await t.validate_input({"questions": qs}, _make_ctx())

    @pytest.mark.asyncio
    async def test_invalid_question_type(self) -> None:
        from kernel.tools.types import ToolInputError

        t = AskUserQuestionTool()
        qs = [_make_text_question()]
        qs[0]["type"] = "number"
        with pytest.raises(ToolInputError, match="type"):
            await t.validate_input({"questions": qs}, _make_ctx())

    @pytest.mark.asyncio
    async def test_invalid_text_question_placeholder(self) -> None:
        from kernel.tools.types import ToolInputError

        t = AskUserQuestionTool()
        qs = [_make_text_question()]
        qs[0]["placeholder"] = 123
        with pytest.raises(ToolInputError, match="placeholder"):
            await t.validate_input({"questions": qs}, _make_ctx())

    @pytest.mark.asyncio
    async def test_invalid_text_question_multiline(self) -> None:
        from kernel.tools.types import ToolInputError

        t = AskUserQuestionTool()
        qs = [_make_text_question()]
        qs[0]["multiline"] = "yes"
        with pytest.raises(ToolInputError, match="multiline"):
            await t.validate_input({"questions": qs}, _make_ctx())

    @pytest.mark.asyncio
    async def test_invalid_text_question_max_length(self) -> None:
        from kernel.tools.types import ToolInputError

        t = AskUserQuestionTool()
        qs = [_make_text_question()]
        qs[0]["maxLength"] = 0
        with pytest.raises(ToolInputError, match="maxLength"):
            await t.validate_input({"questions": qs}, _make_ctx())

    @pytest.mark.asyncio
    async def test_missing_option_label(self) -> None:
        from kernel.tools.types import ToolInputError

        t = AskUserQuestionTool()
        qs = _make_questions(1)
        del qs[0]["options"][0]["label"]
        with pytest.raises(ToolInputError, match="label.*required"):
            await t.validate_input({"questions": qs}, _make_ctx())


# ---------------------------------------------------------------------------
# call() — answer formatting
# ---------------------------------------------------------------------------


class TestCall:
    @pytest.mark.asyncio
    async def test_formats_answers(self) -> None:
        t = AskUserQuestionTool()
        input_ = _make_input(
            answers={"Question 0?": "Option A0"},
        )
        result = await _run(t, input_)
        assert result.data["answers"] == {"Question 0?": "Option A0"}
        # LLM text should contain the answer.
        llm_text = result.llm_content[0].text
        assert "Option A0" in llm_text
        assert "Question 0?" in llm_text

    @pytest.mark.asyncio
    async def test_formats_text_answer(self) -> None:
        t = AskUserQuestionTool()
        question = _make_text_question()
        input_ = _make_input(
            questions=[question],
            answers={"What should the project name be?": "Mustang Studio"},
        )
        result = await _run(t, input_)
        llm_text = result.llm_content[0].text
        assert "What should the project name be?" in llm_text
        assert "Mustang Studio" in llm_text

    @pytest.mark.asyncio
    async def test_no_answers(self) -> None:
        t = AskUserQuestionTool()
        input_ = _make_input()
        result = await _run(t, input_)
        llm_text = result.llm_content[0].text
        assert "(no answer)" in llm_text

    @pytest.mark.asyncio
    async def test_multiple_questions(self) -> None:
        t = AskUserQuestionTool()
        qs = _make_questions(3)
        answers = {
            "Question 0?": "Answer 0",
            "Question 1?": "Answer 1",
            "Question 2?": "Answer 2",
        }
        input_ = _make_input(questions=qs, answers=answers)
        result = await _run(t, input_)
        llm_text = result.llm_content[0].text
        for i in range(3):
            assert f"Question {i}?" in llm_text
            assert f"Answer {i}" in llm_text

    @pytest.mark.asyncio
    async def test_annotation_notes_included(self) -> None:
        t = AskUserQuestionTool()
        input_ = _make_input(
            answers={"Question 0?": "Option A0"},
            annotations={
                "Question 0?": {"notes": "I prefer this because of X"},
            },
        )
        result = await _run(t, input_)
        llm_text = result.llm_content[0].text
        assert "I prefer this because of X" in llm_text

    @pytest.mark.asyncio
    async def test_data_contains_questions_and_answers(self) -> None:
        t = AskUserQuestionTool()
        qs = _make_questions(1)
        answers = {"Question 0?": "Option B0"}
        input_ = _make_input(questions=qs, answers=answers)
        result = await _run(t, input_)
        assert result.data["questions"] == qs
        assert result.data["answers"] == answers

    @pytest.mark.asyncio
    async def test_display_is_text(self) -> None:
        from kernel.tools.types import TextDisplay

        t = AskUserQuestionTool()
        input_ = _make_input(answers={"Question 0?": "Option A0"})
        result = await _run(t, input_)
        assert isinstance(result.display, TextDisplay)


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    def test_deferred_by_default(self) -> None:
        reg = ToolRegistry()
        reg.register(AskUserQuestionTool(), layer="deferred")
        snap = reg.snapshot()
        assert "AskUserQuestion" in snap.deferred_names
        schema_names = {s.name for s in snap.schemas}
        assert "AskUserQuestion" not in schema_names

    def test_survives_plan_mode(self) -> None:
        """kind=other is not mutating, so AskUserQuestion should be
        available in plan mode."""
        reg = ToolRegistry()
        reg.register(AskUserQuestionTool(), layer="deferred")
        snap = reg.snapshot(plan_mode=True)
        assert "AskUserQuestion" in snap.deferred_names

    def test_promoted_appears_in_schemas(self) -> None:
        reg = ToolRegistry()
        reg.register(AskUserQuestionTool(), layer="deferred")
        reg.promote("AskUserQuestion")
        snap = reg.snapshot()
        schema_names = {s.name for s in snap.schemas}
        assert "AskUserQuestion" in schema_names


# ---------------------------------------------------------------------------
# ToolExecutor integration — updated_input forwarding
# ---------------------------------------------------------------------------


class TestPermissionRoundTrip:
    """Verify that PermissionResponse.updated_input flows through
    ToolExecutor → PermissionAllow.updated_input → effective_input → call().
    """

    @pytest.mark.asyncio
    async def test_updated_input_forwarded(self) -> None:
        from unittest.mock import MagicMock

        from kernel.llm.types import ToolUseContent
        from kernel.orchestrator.events import ToolCallResult as ToolCallResultEvent
        from kernel.orchestrator.tool_executor import ToolExecutor
        from kernel.orchestrator.types import OrchestratorDeps, PermissionResponse
        from kernel.tool_authz.types import PermissionAsk, ReasonDefaultRisk

        tool = AskUserQuestionTool()

        # Authorizer always returns "ask" (like the tool's default_risk).
        class _AskAuth:
            async def authorize(self, **kw: Any) -> PermissionAsk:
                return PermissionAsk(
                    message="Answer questions?",
                    decision_reason=ReasonDefaultRisk(
                        risk="low", reason="ask user", tool_name="AskUserQuestion"
                    ),
                )

            def grant(self, **kw: Any) -> None:
                pass

        tool_source = MagicMock()
        tool_source.lookup.return_value = tool
        tool_source.file_state.return_value = MagicMock()

        deps = OrchestratorDeps(
            provider=MagicMock(),
            tool_source=tool_source,
            authorizer=_AskAuth(),
        )

        executor = ToolExecutor(
            deps=deps,
            session_id="test",
            cwd=Path.cwd(),
        )

        questions = _make_questions(1)
        tc = ToolUseContent(
            id="tc-1",
            name="AskUserQuestion",
            input={"questions": questions},
        )
        executor.add_tool(tc)
        executor.finalize_stream()

        # The on_permission callback simulates the client returning answers.
        answers = {"Question 0?": "Option A0"}

        async def on_permission(req: Any) -> PermissionResponse:
            return PermissionResponse(
                decision="allow_once",
                updated_input={
                    "questions": questions,
                    "answers": answers,
                },
            )

        events = []
        async for event, result in executor.results(on_permission=on_permission, mode="default"):
            events.append(event)

        # Should have ToolCallStart + ToolCallResult.
        result_events = [e for e in events if isinstance(e, ToolCallResultEvent)]
        assert len(result_events) == 1

        # The result text should contain the answer that came via
        # updated_input, not "(no answer)".
        result_text = str(result_events[0].content)
        assert "Option A0" in result_text
        assert "(no answer)" not in result_text

    @pytest.mark.asyncio
    async def test_text_question_updated_input_forwarded(self) -> None:
        from unittest.mock import MagicMock

        from kernel.llm.types import ToolUseContent
        from kernel.orchestrator.events import ToolCallResult as ToolCallResultEvent
        from kernel.orchestrator.tool_executor import ToolExecutor
        from kernel.orchestrator.types import OrchestratorDeps, PermissionResponse
        from kernel.tool_authz.types import PermissionAsk, ReasonDefaultRisk

        tool = AskUserQuestionTool()

        class _AskAuth:
            async def authorize(self, **kw: Any) -> PermissionAsk:
                return PermissionAsk(
                    message="Answer questions?",
                    decision_reason=ReasonDefaultRisk(
                        risk="low", reason="ask user", tool_name="AskUserQuestion"
                    ),
                )

            def grant(self, **kw: Any) -> None:
                pass

        tool_source = MagicMock()
        tool_source.lookup.return_value = tool
        tool_source.file_state.return_value = MagicMock()

        executor = ToolExecutor(
            deps=OrchestratorDeps(
                provider=MagicMock(),
                tool_source=tool_source,
                authorizer=_AskAuth(),
            ),
            session_id="test",
            cwd=Path.cwd(),
        )

        questions = [_make_text_question()]
        executor.add_tool(
            ToolUseContent(
                id="tc-1",
                name="AskUserQuestion",
                input={"questions": questions},
            )
        )
        executor.finalize_stream()

        async def on_permission(req: Any) -> PermissionResponse:
            return PermissionResponse(
                decision="allow_once",
                updated_input={
                    "questions": questions,
                    "answers": {"What should the project name be?": "Mustang Studio"},
                },
            )

        events = []
        async for event, _result in executor.results(on_permission=on_permission, mode="default"):
            events.append(event)

        result_events = [e for e in events if isinstance(e, ToolCallResultEvent)]
        assert len(result_events) == 1
        result_text = str(result_events[0].content)
        assert "Mustang Studio" in result_text
        assert "(no answer)" not in result_text
