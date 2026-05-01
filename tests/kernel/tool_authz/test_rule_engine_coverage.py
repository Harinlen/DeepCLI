"""Extended RuleEngine tests — targeting uncovered branches.

Covers: MCP server-level rules, wildcard mcp__* rules, matcher
delegation with rule_content, deny short-circuit, ask/allow
precedence, and no-rules fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.tool_authz.rule_engine import RuleEngine, _is_mcp_server_rule, _rule_matches_tool
from kernel.tool_authz.rule_parser import parse_rule
from kernel.tool_authz.types import RuleSource
from kernel.tools.tool import Tool
from kernel.tools.types import PermissionSuggestion, ToolCallResult
from kernel.orchestrator.types import ToolKind


# ---------------------------------------------------------------------------
# Fake tools
# ---------------------------------------------------------------------------


class _FakeTool(Tool[dict[str, Any], str]):
    name = "FakeTool"
    description = "test tool"
    kind = ToolKind.other

    async def call(self, input, ctx):
        yield ToolCallResult(data={}, llm_content=[], display=None)  # type: ignore[arg-type]


class _FakeToolWithAlias(Tool[dict[str, Any], str]):
    name = "NewName"
    aliases = ("OldName",)
    description = "tool with alias"
    kind = ToolKind.other

    async def call(self, input, ctx):
        yield ToolCallResult(data={}, llm_content=[], display=None)  # type: ignore[arg-type]


class _FakeBash(Tool[dict[str, Any], str]):
    name = "Bash"
    description = "bash"
    kind = ToolKind.other

    def prepare_permission_matcher(self, input):
        cmd = str(input.get("command", ""))
        from fnmatch import fnmatch
        return lambda pattern: fnmatch(cmd, pattern)

    def default_risk(self, input, ctx):
        return PermissionSuggestion(risk="medium", default_decision="ask", reason="bash")

    async def call(self, input, ctx):
        yield ToolCallResult(data={}, llm_content=[], display=None)  # type: ignore[arg-type]


class _FakeMCPTool(Tool[dict[str, Any], str]):
    name = "mcp__slack__send"
    description = "MCP slack send"
    kind = ToolKind.other

    async def call(self, input, ctx):
        yield ToolCallResult(data={}, llm_content=[], display=None)  # type: ignore[arg-type]


@dataclass(frozen=True)
class _FakeAuthCtx:
    cwd: Path = Path("/tmp/test")
    session_id: str = "test"


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------


engine = RuleEngine()
ctx = _FakeAuthCtx()


class TestNoRules:
    def test_no_rules_returns_none_behavior(self) -> None:
        outcome = engine.decide([], _FakeTool(), {}, ctx)
        assert outcome.matched_rule is None
        assert outcome.rule_behavior is None

    def test_always_calls_default_risk(self) -> None:
        outcome = engine.decide([], _FakeBash(), {"command": "ls"}, ctx)
        assert outcome.suggestion.risk == "medium"


class TestDenyShortCircuit:
    def test_deny_stops_evaluation(self) -> None:
        rules = [
            parse_rule("FakeTool", "deny", RuleSource.USER, 0),
            parse_rule("FakeTool", "allow", RuleSource.USER, 1),
        ]
        outcome = engine.decide(rules, _FakeTool(), {}, ctx)
        assert outcome.rule_behavior == "deny"

    def test_deny_with_content(self) -> None:
        rules = [parse_rule("Bash(git *)", "deny", RuleSource.USER, 0)]
        outcome = engine.decide(rules, _FakeBash(), {"command": "git push"}, ctx)
        assert outcome.rule_behavior == "deny"


class TestAskAllowPrecedence:
    def test_ask_overrides_allow(self) -> None:
        rules = [
            parse_rule("FakeTool", "allow", RuleSource.USER, 0),
            parse_rule("FakeTool", "ask", RuleSource.USER, 1),
        ]
        outcome = engine.decide(rules, _FakeTool(), {}, ctx)
        assert outcome.rule_behavior == "ask"

    def test_allow_when_no_ask(self) -> None:
        rules = [parse_rule("FakeTool", "allow", RuleSource.USER, 0)]
        outcome = engine.decide(rules, _FakeTool(), {}, ctx)
        assert outcome.rule_behavior == "allow"


class TestContentMatching:
    def test_content_matches(self) -> None:
        rules = [parse_rule("Bash(git *)", "allow", RuleSource.USER, 0)]
        outcome = engine.decide(rules, _FakeBash(), {"command": "git status"}, ctx)
        assert outcome.rule_behavior == "allow"

    def test_content_no_match(self) -> None:
        rules = [parse_rule("Bash(git *)", "allow", RuleSource.USER, 0)]
        outcome = engine.decide(rules, _FakeBash(), {"command": "rm -rf /"}, ctx)
        assert outcome.rule_behavior is None

    def test_matcher_cache_reused(self) -> None:
        """Two content rules for the same tool reuse the matcher."""
        rules = [
            parse_rule("Bash(git *)", "allow", RuleSource.USER, 0),
            parse_rule("Bash(npm *)", "allow", RuleSource.USER, 1),
        ]
        outcome = engine.decide(rules, _FakeBash(), {"command": "git push"}, ctx)
        assert outcome.rule_behavior == "allow"


class TestAliasMatching:
    def test_alias_matches(self) -> None:
        rules = [parse_rule("OldName", "allow", RuleSource.USER, 0)]
        outcome = engine.decide(rules, _FakeToolWithAlias(), {}, ctx)
        assert outcome.rule_behavior == "allow"


class TestMCPRules:
    def test_server_level_rule_matches_tool(self) -> None:
        rules = [parse_rule("mcp__slack", "allow", RuleSource.USER, 0)]
        outcome = engine.decide(rules, _FakeMCPTool(), {}, ctx)
        assert outcome.rule_behavior == "allow"

    def test_wildcard_mcp_matches(self) -> None:
        rules = [parse_rule("mcp__*", "deny", RuleSource.USER, 0)]
        outcome = engine.decide(rules, _FakeMCPTool(), {}, ctx)
        assert outcome.rule_behavior == "deny"

    def test_wildcard_doesnt_match_non_mcp(self) -> None:
        rules = [parse_rule("mcp__*", "deny", RuleSource.USER, 0)]
        outcome = engine.decide(rules, _FakeTool(), {}, ctx)
        assert outcome.rule_behavior is None

    def test_server_rule_doesnt_match_non_mcp(self) -> None:
        rules = [parse_rule("mcp__slack", "allow", RuleSource.USER, 0)]
        outcome = engine.decide(rules, _FakeTool(), {}, ctx)
        assert outcome.rule_behavior is None


class TestDestructiveFlag:
    def test_non_destructive_tool(self) -> None:
        outcome = engine.decide([], _FakeTool(), {}, ctx)
        assert outcome.is_destructive is False


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestRuleMatchesTool:
    def test_primary_name(self) -> None:
        rule = parse_rule("FakeTool", "allow", RuleSource.USER, 0)
        assert _rule_matches_tool(rule, _FakeTool())

    def test_alias(self) -> None:
        rule = parse_rule("OldName", "allow", RuleSource.USER, 0)
        assert _rule_matches_tool(rule, _FakeToolWithAlias())

    def test_no_match(self) -> None:
        rule = parse_rule("Other", "allow", RuleSource.USER, 0)
        assert not _rule_matches_tool(rule, _FakeTool())

    def test_mcp_server_level(self) -> None:
        rule = parse_rule("mcp__slack", "allow", RuleSource.USER, 0)
        assert _rule_matches_tool(rule, _FakeMCPTool())

    def test_mcp_wildcard(self) -> None:
        rule = parse_rule("mcp__*", "allow", RuleSource.USER, 0)
        assert _rule_matches_tool(rule, _FakeMCPTool())


class TestIsMCPServerRule:
    def test_server_rule(self) -> None:
        assert _is_mcp_server_rule("mcp__slack", _FakeMCPTool())

    def test_tool_rule(self) -> None:
        # mcp__slack__send is a tool rule, not a server rule
        assert not _is_mcp_server_rule("mcp__slack__send", _FakeMCPTool())

    def test_non_mcp(self) -> None:
        assert not _is_mcp_server_rule("Bash", _FakeTool())
