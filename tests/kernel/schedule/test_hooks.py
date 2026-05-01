"""Tests for cron hook event definitions."""

from __future__ import annotations

from kernel.hooks.types import EVENT_SPECS, HookEvent


class TestCronHookEvents:
    """Verify PRE_CRON_FIRE and POST_CRON_FIRE are properly registered."""

    def test_pre_cron_fire_in_enum(self) -> None:
        assert HookEvent.PRE_CRON_FIRE.value == "pre_cron_fire"

    def test_post_cron_fire_in_enum(self) -> None:
        assert HookEvent.POST_CRON_FIRE.value == "post_cron_fire"

    def test_pre_cron_fire_in_specs(self) -> None:
        spec = EVENT_SPECS[HookEvent.PRE_CRON_FIRE]
        assert spec.can_block is False
        assert spec.accepts_input_mutation is False

    def test_post_cron_fire_in_specs(self) -> None:
        spec = EVENT_SPECS[HookEvent.POST_CRON_FIRE]
        assert spec.can_block is False
        assert spec.accepts_input_mutation is False

    def test_total_event_count(self) -> None:
        """14 original + 2 cron + 2 worktree = 18 events."""
        assert len(HookEvent) == 18
        assert len(EVENT_SPECS) == 18
