"""RuleParser — DSL → PermissionRule + fail-closed on malformed input."""

from __future__ import annotations

from kernel.tool_authz.rule_parser import parse_rule
from kernel.tool_authz.types import RuleSource


def test_tool_only_rule() -> None:
    rule = parse_rule("Bash", "allow", RuleSource.USER, 0)
    assert rule.value.tool_name == "Bash"
    assert rule.value.rule_content is None
    assert rule.behavior == "allow"
    assert rule.rule_id == "user:0"


def test_content_scoped_rule() -> None:
    rule = parse_rule("Bash(git:*)", "allow", RuleSource.USER, 3)
    assert rule.value.tool_name == "Bash"
    assert rule.value.rule_content == "git:*"
    assert rule.raw_dsl == "Bash(git:*)"


def test_escape_sequences_are_unescaped() -> None:
    rule = parse_rule(r"Bash(rm -rf \(danger\))", "deny", RuleSource.USER, 0)
    assert rule.value.rule_content == "rm -rf (danger)"


def test_trailing_backslash_is_rejected() -> None:
    rule = parse_rule("Bash(ab\\)", "allow", RuleSource.USER, 0)
    # Malformed rule becomes an inert deny — tool_name doesn't match any real tool.
    assert rule.value.tool_name == "<unparsed>"
    assert rule.behavior == "deny"


def test_unescaped_paren_in_content_is_rejected() -> None:
    rule = parse_rule("Bash(a(b))", "allow", RuleSource.USER, 0)
    assert rule.value.tool_name == "<unparsed>"


def test_missing_closing_paren_is_rejected() -> None:
    rule = parse_rule("Bash(git:*", "allow", RuleSource.USER, 0)
    assert rule.value.tool_name == "<unparsed>"


def test_empty_rule_is_rejected() -> None:
    rule = parse_rule("", "allow", RuleSource.USER, 0)
    assert rule.value.tool_name == "<unparsed>"


def test_empty_content_is_rejected() -> None:
    rule = parse_rule("Bash()", "allow", RuleSource.USER, 0)
    assert rule.value.tool_name == "<unparsed>"


def test_mcp_server_level_rule() -> None:
    rule = parse_rule("mcp__slack", "deny", RuleSource.USER, 0)
    assert rule.value.tool_name == "mcp__slack"
    assert rule.value.rule_content is None


def test_rule_id_tracks_source_and_index() -> None:
    rule = parse_rule("Bash", "allow", RuleSource.FLAG, 7)
    assert rule.rule_id == "flag:7"
    assert rule.source == RuleSource.FLAG
    assert rule.layer_index == 7
