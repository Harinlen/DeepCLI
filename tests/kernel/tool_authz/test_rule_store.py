"""Tests for RuleStore — layered rule snapshot with config and flag layers."""

from __future__ import annotations

from kernel.tool_authz.rule_store import RuleStore, _build_rules, _parse_section
from kernel.tool_authz.types import RuleSource
from kernel.tool_authz.config_section import PermissionsSection


# ---------------------------------------------------------------------------
# _build_rules
# ---------------------------------------------------------------------------


class TestBuildRules:
    def test_empty_lists(self) -> None:
        rules = _build_rules(allow=[], deny=[], ask=[], source=RuleSource.USER)
        assert rules == []

    def test_ordering_deny_ask_allow(self) -> None:
        rules = _build_rules(
            allow=["Bash"],
            deny=["FileWrite"],
            ask=["FileEdit"],
            source=RuleSource.USER,
        )
        assert len(rules) == 3
        assert rules[0].behavior == "deny"
        assert rules[1].behavior == "ask"
        assert rules[2].behavior == "allow"

    def test_sequential_indices(self) -> None:
        rules = _build_rules(
            allow=["A", "B"],
            deny=["C"],
            ask=[],
            source=RuleSource.FLAG,
        )
        indices = [r.layer_index for r in rules]
        assert indices == [0, 1, 2]

    def test_source_propagated(self) -> None:
        rules = _build_rules(allow=["Bash"], deny=[], ask=[], source=RuleSource.FLAG)
        assert all(r.source == RuleSource.FLAG for r in rules)


# ---------------------------------------------------------------------------
# _parse_section
# ---------------------------------------------------------------------------


class TestParseSection:
    def test_parses_section(self) -> None:
        section = PermissionsSection(allow=["Bash(git:*)"], deny=["FileWrite"], ask=[])
        rules = _parse_section(section)
        assert len(rules) == 2
        deny_rules = [r for r in rules if r.behavior == "deny"]
        allow_rules = [r for r in rules if r.behavior == "allow"]
        assert len(deny_rules) == 1
        assert len(allow_rules) == 1
        assert deny_rules[0].value.tool_name == "FileWrite"
        assert allow_rules[0].value.tool_name == "Bash"
        assert allow_rules[0].value.rule_content == "git:*"


# ---------------------------------------------------------------------------
# RuleStore
# ---------------------------------------------------------------------------


class TestRuleStore:
    def test_empty_snapshot(self) -> None:
        store = RuleStore()
        assert store.snapshot() == []

    def test_flag_layer(self) -> None:
        store = RuleStore()
        store.load_flag_layer(allow=["Bash"], deny=["FileWrite"], ask=["FileEdit"])
        rules = store.snapshot()
        assert len(rules) == 3
        assert all(r.source == RuleSource.FLAG for r in rules)

    def test_config_then_flag_order(self) -> None:
        """Config rules come first, flag rules come after."""
        store = RuleStore()

        # Simulate config binding
        section = PermissionsSection(allow=["ConfigAllow"], deny=[], ask=[])
        store._config_rules = _parse_section(section)

        store.load_flag_layer(allow=["FlagAllow"], deny=[], ask=[])

        rules = store.snapshot()
        assert len(rules) == 2
        assert rules[0].source == RuleSource.USER  # config
        assert rules[1].source == RuleSource.FLAG   # flag

    def test_flag_layer_is_frozen(self) -> None:
        """Calling load_flag_layer again replaces — not appends."""
        store = RuleStore()
        store.load_flag_layer(allow=["A"], deny=[], ask=[])
        assert len(store.snapshot()) == 1
        store.load_flag_layer(allow=["B", "C"], deny=[], ask=[])
        assert len(store.snapshot()) == 2

    def test_multiple_rules_per_behavior(self) -> None:
        store = RuleStore()
        store.load_flag_layer(
            allow=["Bash(git:*)", "Bash(npm:*)"],
            deny=["Bash(rm:*)"],
            ask=[],
        )
        rules = store.snapshot()
        assert len(rules) == 3
        deny_rules = [r for r in rules if r.behavior == "deny"]
        allow_rules = [r for r in rules if r.behavior == "allow"]
        assert len(deny_rules) == 1
        assert len(allow_rules) == 2
