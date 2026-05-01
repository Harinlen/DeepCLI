"""Tests for the ThinkingConfig parser (Step 5.1)."""

from __future__ import annotations

from daemon.providers.thinking_config import parse_thinking, to_anthropic_param


class TestParseThinking:
    def test_none_is_adaptive(self) -> None:
        mode, budget = parse_thinking(None)
        assert mode == "adaptive"
        assert budget > 0

    def test_adaptive_string(self) -> None:
        assert parse_thinking("adaptive")[0] == "adaptive"

    def test_disabled(self) -> None:
        mode, budget = parse_thinking("disabled")
        assert mode == "disabled"
        assert budget == 0

    def test_enabled_string(self) -> None:
        assert parse_thinking("enabled")[0] == "enabled"

    def test_integer_budget(self) -> None:
        mode, budget = parse_thinking(2000)
        assert mode == "enabled"
        assert budget == 2000

    def test_integer_below_min_clamps_up(self) -> None:
        mode, budget = parse_thinking(100)
        assert mode == "enabled"
        assert budget == 1024

    def test_integer_above_max_clamps_down(self) -> None:
        mode, budget = parse_thinking(999_999)
        assert mode == "enabled"
        assert budget == 32_000

    def test_unknown_string_falls_back(self) -> None:
        assert parse_thinking("bananas")[0] == "adaptive"


class TestToAnthropicParam:
    def test_adaptive_returns_none(self) -> None:
        assert to_anthropic_param(None) is None
        assert to_anthropic_param("adaptive") is None

    def test_disabled(self) -> None:
        assert to_anthropic_param("disabled") == {"type": "disabled"}

    def test_enabled_with_budget(self) -> None:
        result = to_anthropic_param(4096)
        assert result == {"type": "enabled", "budget_tokens": 4096}
