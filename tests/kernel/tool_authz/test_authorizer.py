"""ToolAuthorizer end-to-end — the 4 most critical decision paths.

Uses a :class:`FakeTool` + in-memory :class:`RuleStore` rather than a
real ConfigManager, so the tests stay focused on the decision logic.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from kernel.connection_auth import AuthContext
from kernel.orchestrator.types import ToolKind
from kernel.tool_authz.authorizer import ToolAuthorizer
from kernel.tool_authz.rule_parser import parse_rule
from kernel.tool_authz.types import (
    AuthorizeContext,
    PermissionAllow,
    PermissionAsk,
    PermissionDeny,
    RuleSource,
)
from kernel.tools.tool import Tool
from kernel.tools.types import (
    PermissionSuggestion,
    ToolCallProgress,
    ToolCallResult,
)


class FakeTool(Tool[dict[str, Any], str]):
    """Minimal Tool for authorizer tests.  Safe, non-destructive."""

    name = "Echo"
    description = "test"
    kind = ToolKind.read

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        return PermissionSuggestion(risk="low", default_decision="allow", reason="test default")

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    def prepare_permission_matcher(self, input: dict[str, Any]):  # noqa: ANN201
        text = str(input.get("text", ""))
        return lambda pattern: text == pattern

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        yield ToolCallResult(
            data=input,
            llm_content=[],
            display=None,  # type: ignore[arg-type]
        )


@pytest.fixture
def fake_auth() -> AuthContext:
    from datetime import datetime, timezone

    return AuthContext(
        connection_id="test",
        credential_type="token",
        remote_addr="127.0.0.1:1",
        authenticated_at=datetime.now(timezone.utc),
    )


def _ctx(
    fake_auth: AuthContext, mode: str = "default", avoid_prompts: bool = False
) -> AuthorizeContext:
    return AuthorizeContext(
        session_id="s-1",
        agent_depth=0,
        mode=mode,  # type: ignore[arg-type]
        cwd=Path.cwd(),
        connection_auth=fake_auth,
        should_avoid_prompts=avoid_prompts,
    )


@pytest.fixture
def authorizer() -> ToolAuthorizer:
    # Bypass Subsystem.load (which needs a module_table) by constructing directly.
    class _FakeModuleTable:
        config = None
        flags = None

    authz = ToolAuthorizer.__new__(ToolAuthorizer)
    authz._module_table = _FakeModuleTable()  # type: ignore[attr-defined]

    from kernel.tool_authz.bash_classifier import BashClassifier
    from kernel.tool_authz.rule_engine import RuleEngine
    from kernel.tool_authz.rule_store import RuleStore
    from kernel.tool_authz.session_grant_cache import SessionGrantCache

    authz._rule_store = RuleStore()
    authz._rule_engine = RuleEngine()
    authz._grant_cache = SessionGrantCache()
    authz._bash_classifier = BashClassifier(enabled=False)
    return authz


# ---------------------------------------------------------------------------
# Decision paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_default_risk_allows_when_no_rules(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    tool = FakeTool()
    decision = await authorizer.authorize(tool=tool, tool_input={"text": "hi"}, ctx=_ctx(fake_auth))
    assert isinstance(decision, PermissionAllow)


@pytest.mark.anyio
async def test_deny_rule_wins_over_default_risk(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    authorizer._rule_store._config_rules = [parse_rule("Echo", "deny", RuleSource.USER, 0)]
    tool = FakeTool()
    decision = await authorizer.authorize(tool=tool, tool_input={"text": "hi"}, ctx=_ctx(fake_auth))
    assert isinstance(decision, PermissionDeny)


@pytest.mark.anyio
async def test_ask_rule_produces_permission_ask(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    authorizer._rule_store._config_rules = [parse_rule("Echo", "ask", RuleSource.USER, 0)]
    tool = FakeTool()
    decision = await authorizer.authorize(tool=tool, tool_input={"text": "hi"}, ctx=_ctx(fake_auth))
    assert isinstance(decision, PermissionAsk)
    outcomes = [s.outcome for s in decision.suggestions]
    assert "allow_once" in outcomes
    assert "allow_always" in outcomes
    assert "deny" in outcomes


@pytest.mark.anyio
async def test_session_grant_short_circuits(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    """A previously-granted input is allowed without re-consulting rules."""
    authorizer._rule_store._config_rules = [parse_rule("Echo", "deny", RuleSource.USER, 0)]
    tool = FakeTool()
    authorizer._grant_cache.on_session_open("s-1")
    authorizer._grant_cache.grant(session_id="s-1", tool=tool, tool_input={"text": "x"})

    decision = await authorizer.authorize(tool=tool, tool_input={"text": "x"}, ctx=_ctx(fake_auth))
    assert isinstance(decision, PermissionAllow)


@pytest.mark.anyio
async def test_plan_mode_denies_mutating_tool(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    class WriteTool(FakeTool):
        name = "Writer"
        kind = ToolKind.edit

    decision = await authorizer.authorize(
        tool=WriteTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth, mode="plan")
    )
    assert isinstance(decision, PermissionDeny)
    assert "plan" in decision.message.lower()


@pytest.mark.anyio
async def test_plan_mode_does_not_deny_read_tool(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    """Read-only tools pass through plan mode unchanged."""
    decision = await authorizer.authorize(
        tool=FakeTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth, mode="plan")
    )
    assert isinstance(decision, PermissionAllow)


@pytest.mark.anyio
async def test_bypass_mode_allows_everything(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    authorizer._rule_store._config_rules = [parse_rule("Echo", "deny", RuleSource.USER, 0)]
    decision = await authorizer.authorize(
        tool=FakeTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth, mode="bypass")
    )
    assert isinstance(decision, PermissionAllow)


@pytest.mark.anyio
async def test_should_avoid_prompts_converts_ask_to_deny(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    authorizer._rule_store._config_rules = [parse_rule("Echo", "ask", RuleSource.USER, 0)]
    decision = await authorizer.authorize(
        tool=FakeTool(),
        tool_input={"text": "x"},
        ctx=_ctx(fake_auth, avoid_prompts=True),
    )
    assert isinstance(decision, PermissionDeny)


@pytest.mark.anyio
async def test_destructive_tool_excludes_allow_always_button(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    class DangerousTool(FakeTool):
        name = "Dangerous"

        def is_destructive(self, _input: dict[str, Any]) -> bool:
            return True

    authorizer._rule_store._config_rules = [parse_rule("Dangerous", "ask", RuleSource.USER, 0)]
    decision = await authorizer.authorize(
        tool=DangerousTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth)
    )
    assert isinstance(decision, PermissionAsk)
    outcomes = [s.outcome for s in decision.suggestions]
    assert "allow_always" not in outcomes
    assert "allow_once" in outcomes


# ---------------------------------------------------------------------------
# filter_denied_tools
# ---------------------------------------------------------------------------


def test_filter_denied_tools_blocks_exact_match(authorizer: ToolAuthorizer) -> None:
    authorizer._rule_store._config_rules = [parse_rule("Echo", "deny", RuleSource.USER, 0)]
    denied = authorizer.filter_denied_tools({"Echo", "Bash"})
    assert denied == {"Echo"}


def test_filter_denied_tools_blocks_mcp_server(authorizer: ToolAuthorizer) -> None:
    authorizer._rule_store._config_rules = [parse_rule("mcp__slack", "deny", RuleSource.USER, 0)]
    denied = authorizer.filter_denied_tools(
        {"mcp__slack__send", "mcp__slack__list", "mcp__github__pr_list"}
    )
    assert denied == {"mcp__slack__send", "mcp__slack__list"}


# ---------------------------------------------------------------------------
# accept_edits mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_accept_edits_allows_edit_kind(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    """accept_edits mode auto-allows ToolKind.edit without asking."""

    class EditTool(FakeTool):
        name = "Editor"
        kind = ToolKind.edit

    decision = await authorizer.authorize(
        tool=EditTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth, mode="accept_edits")
    )
    assert isinstance(decision, PermissionAllow)
    assert decision.decision_reason.type == "mode"
    assert decision.decision_reason.mode == "accept_edits"  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_accept_edits_does_not_allow_execute(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    """accept_edits mode does NOT auto-allow ToolKind.execute — falls through to normal flow."""

    class ExecTool(FakeTool):
        name = "Runner"
        kind = ToolKind.execute

        def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
            return PermissionSuggestion(risk="medium", default_decision="ask", reason="test")

    authorizer._rule_store._config_rules = [parse_rule("Runner", "ask", RuleSource.USER, 0)]
    decision = await authorizer.authorize(
        tool=ExecTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth, mode="accept_edits")
    )
    assert isinstance(decision, PermissionAsk)


@pytest.mark.anyio
async def test_accept_edits_does_not_allow_delete(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    """accept_edits mode does NOT auto-allow ToolKind.delete."""

    class DelTool(FakeTool):
        name = "Deleter"
        kind = ToolKind.delete

        def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
            return PermissionSuggestion(risk="medium", default_decision="ask", reason="test")

    authorizer._rule_store._config_rules = [parse_rule("Deleter", "ask", RuleSource.USER, 0)]
    decision = await authorizer.authorize(
        tool=DelTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth, mode="accept_edits")
    )
    assert isinstance(decision, PermissionAsk)


# ---------------------------------------------------------------------------
# auto mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_auto_allows_low_risk(authorizer: ToolAuthorizer, fake_auth: AuthContext) -> None:
    """auto mode auto-allows tools whose default_risk is 'low' when result would be 'ask'."""
    authorizer._rule_store._config_rules = [parse_rule("Echo", "ask", RuleSource.USER, 0)]
    # FakeTool.default_risk returns (low, allow) — but the ask rule forces into _handle_ask.
    decision = await authorizer.authorize(
        tool=FakeTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth, mode="auto")
    )
    assert isinstance(decision, PermissionAllow)
    assert decision.decision_reason.type == "mode"
    assert decision.decision_reason.mode == "auto"  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_auto_does_not_allow_high_risk(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    """auto mode does NOT auto-allow tools whose default_risk is 'high'."""

    class HighRiskTool(FakeTool):
        name = "HighRisk"

        def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
            return PermissionSuggestion(risk="high", default_decision="ask", reason="dangerous")

    authorizer._rule_store._config_rules = [parse_rule("HighRisk", "ask", RuleSource.USER, 0)]
    decision = await authorizer.authorize(
        tool=HighRiskTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth, mode="auto")
    )
    assert isinstance(decision, PermissionAsk)


@pytest.mark.anyio
async def test_auto_medium_risk_falls_through(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    """auto mode does NOT auto-allow medium-risk tools — they go through normal ask flow."""

    class MediumRiskTool(FakeTool):
        name = "MedRisk"

        def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
            return PermissionSuggestion(risk="medium", default_decision="ask", reason="unknown")

    authorizer._rule_store._config_rules = [parse_rule("MedRisk", "ask", RuleSource.USER, 0)]
    decision = await authorizer.authorize(
        tool=MediumRiskTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth, mode="auto")
    )
    assert isinstance(decision, PermissionAsk)


@pytest.mark.anyio
async def test_auto_respects_deny_rules(authorizer: ToolAuthorizer, fake_auth: AuthContext) -> None:
    """Deny rules still apply in auto mode — auto only affects ask decisions."""
    authorizer._rule_store._config_rules = [parse_rule("Echo", "deny", RuleSource.USER, 0)]
    decision = await authorizer.authorize(
        tool=FakeTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth, mode="auto")
    )
    assert isinstance(decision, PermissionDeny)


# ---------------------------------------------------------------------------
# dont_ask mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dont_ask_denies_ask_decisions(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    """dont_ask mode converts any ask decision into deny with ReasonMode."""
    authorizer._rule_store._config_rules = [parse_rule("Echo", "ask", RuleSource.USER, 0)]
    decision = await authorizer.authorize(
        tool=FakeTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth, mode="dont_ask")
    )
    assert isinstance(decision, PermissionDeny)
    assert decision.decision_reason.type == "mode"
    assert decision.decision_reason.mode == "dont_ask"  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_dont_ask_respects_allow_rules(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    """Allow rules still take effect in dont_ask mode — the tool was pre-approved."""
    authorizer._rule_store._config_rules = [parse_rule("Echo", "allow", RuleSource.USER, 0)]
    decision = await authorizer.authorize(
        tool=FakeTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth, mode="dont_ask")
    )
    assert isinstance(decision, PermissionAllow)


@pytest.mark.anyio
async def test_dont_ask_respects_deny_rules(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    """Deny rules still apply in dont_ask mode."""
    authorizer._rule_store._config_rules = [parse_rule("Echo", "deny", RuleSource.USER, 0)]
    decision = await authorizer.authorize(
        tool=FakeTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth, mode="dont_ask")
    )
    assert isinstance(decision, PermissionDeny)


@pytest.mark.anyio
async def test_dont_ask_denies_default_risk_ask(
    authorizer: ToolAuthorizer, fake_auth: AuthContext
) -> None:
    """dont_ask mode denies even when no rules match and default_risk says ask."""

    class MediumTool(FakeTool):
        name = "Medium"

        def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
            return PermissionSuggestion(risk="medium", default_decision="ask", reason="test")

    decision = await authorizer.authorize(
        tool=MediumTool(), tool_input={"text": "x"}, ctx=_ctx(fake_auth, mode="dont_ask")
    )
    assert isinstance(decision, PermissionDeny)
    assert decision.decision_reason.type == "mode"


def test_filter_denied_tools_skips_content_scoped_rules(
    authorizer: ToolAuthorizer,
) -> None:
    """Rules with content can't be evaluated without a specific input."""
    authorizer._rule_store._config_rules = [parse_rule("Bash(rm:*)", "deny", RuleSource.USER, 0)]
    denied = authorizer.filter_denied_tools({"Bash"})
    assert denied == set()
