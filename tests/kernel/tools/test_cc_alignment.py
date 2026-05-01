"""Canary tests — guard against prompt drift from Claude Code.

These tests do NOT assert Mustang's descriptions are byte-identical to
CC's — they're intentionally adapted (paths, Mustang-specific fields,
Mustang-unsupported features dropped).  They assert that the
**key ideas** we ported from CC are still present.  If one of these
tests fails, someone edited a description and lost a critical CC
behaviour cue — investigate before merging.

When CC itself upgrades, a human maintainer should re-read the
corresponding ``prompt.ts`` and either update the canary or update
the Mustang ``.txt`` file (or both).  There is no fixture snapshot
file — fixtures would go stale faster than they earn their keep.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from kernel.prompts.manager import PromptManager
from kernel.tools.builtin.cron_create import CronCreateTool
from kernel.tools.builtin.enter_plan_mode import EnterPlanModeTool
from kernel.tools.builtin.enter_worktree import EnterWorktreeTool
from kernel.tools.builtin.exit_plan_mode import ExitPlanModeTool
from kernel.tools.builtin.exit_worktree import ExitWorktreeTool
from kernel.tools.builtin.todo_write import TodoWriteTool
from kernel.tools.builtin.web_search import WebSearchTool


@pytest.fixture(scope="module")
def prompts() -> PromptManager:
    pm = PromptManager()
    pm.load()
    return pm


def _desc(cls: type, pm: PromptManager) -> str:
    tool = cls()
    tool._prompt_manager = pm
    return tool.get_description()


# ---------------------------------------------------------------------------
# TodoWrite — the big one: activeForm requirement + 8 worked examples
# ---------------------------------------------------------------------------


class TestTodoWriteAlignment:
    def test_mentions_active_form(self, prompts: PromptManager) -> None:
        """CC's two-form task spec (imperative content + present-continuous
        activeForm) must be taught to the LLM."""
        desc = _desc(TodoWriteTool, prompts)
        assert "activeForm" in desc
        assert "present continuous" in desc

    def test_when_to_use_examples_present(self, prompts: PromptManager) -> None:
        """Four CC examples should appear in some form."""
        desc = _desc(TodoWriteTool, prompts).lower()
        assert "dark mode" in desc                          # ex 1
        assert "getcwd" in desc                             # ex 2: rename function
        assert "e-commerce" in desc or "shopping cart" in desc  # ex 3
        assert "memoization" in desc                        # ex 4: React perf

    def test_when_not_to_use_examples_present(self, prompts: PromptManager) -> None:
        """Four CC negative examples."""
        desc = _desc(TodoWriteTool, prompts).lower()
        assert "hello world" in desc                  # ex 1
        assert "git status" in desc                   # ex 2
        assert "calculatetotal" in desc               # ex 3
        assert "npm install" in desc                  # ex 4

    def test_task_breakdown_section(self, prompts: PromptManager) -> None:
        desc = _desc(TodoWriteTool, prompts)
        assert "Task Breakdown" in desc

    def test_schema_requires_active_form(self) -> None:
        schema = TodoWriteTool().to_schema().input_schema
        item = schema["properties"]["todos"]["items"]
        assert "activeForm" in item["required"]


# ---------------------------------------------------------------------------
# CronCreate — off-minute rationale, durability, jitter specifics, auto-expire
# ---------------------------------------------------------------------------


class TestCronCreateAlignment:
    def test_off_minute_rationale_present(self, prompts: PromptManager) -> None:
        """The ":00/:30" rationale is what persuades the LLM to pick
        off-minutes; losing it means every user's cron lands at the
        same instant."""
        desc = _desc(CronCreateTool, prompts)
        assert ":00 and :30" in desc
        assert "across the planet" in desc or "same instant" in desc

    def test_auto_expire_section(self, prompts: PromptManager) -> None:
        desc = _desc(CronCreateTool, prompts).lower()
        assert "auto-expire" in desc or "auto expire" in desc
        # Mustang-substituted day value (CC: DEFAULT_MAX_AGE_DAYS)
        assert "7 days" in desc.lower() or "7-day" in desc.lower()

    def test_jitter_specifics(self, prompts: PromptManager) -> None:
        desc = _desc(CronCreateTool, prompts)
        assert "10%" in desc
        assert "90" in desc  # 90s one-shot jitter

    def test_durability_section_present(self, prompts: PromptManager) -> None:
        desc = _desc(CronCreateTool, prompts)
        assert "Durability" in desc or "durable" in desc.lower()

    def test_mustang_specific_addenda(self, prompts: PromptManager) -> None:
        """Mustang-only cron fields must be documented."""
        desc = _desc(CronCreateTool, prompts)
        assert "delivery" in desc
        assert "skills" in desc
        assert "repeat_count" in desc


# ---------------------------------------------------------------------------
# WebSearch — dynamic month/year + Sources requirement
# ---------------------------------------------------------------------------


class TestWebSearchAlignment:
    def test_dynamic_month_year_injected(self, prompts: PromptManager) -> None:
        """WebSearch must override get_description() so the LLM sees the
        real current month — otherwise the "use the current year" guidance
        is useless."""
        tool = WebSearchTool()
        tool._prompt_manager = prompts
        desc = tool.get_description()
        now = datetime.now()
        assert str(now.year) in desc
        assert now.strftime("%B") in desc

    def test_sources_requirement(self, prompts: PromptManager) -> None:
        desc = _desc(WebSearchTool, prompts)
        assert 'Sources:' in desc
        assert "MANDATORY" in desc

    def test_us_only_removed(self, prompts: PromptManager) -> None:
        """Mustang multi-backend search is NOT US-only — this CC line
        must not appear."""
        desc = _desc(WebSearchTool, prompts)
        assert "only available in the US" not in desc


# ---------------------------------------------------------------------------
# EnterPlanMode — 7 criteria + What Happens + GOOD/BAD examples
# ---------------------------------------------------------------------------


class TestEnterPlanModeAlignment:
    def test_seven_when_to_use_criteria(self, prompts: PromptManager) -> None:
        desc = _desc(EnterPlanModeTool, prompts)
        # Seven numbered criteria — CC uses "1. **...**" through "7. **...**"
        for n in range(1, 8):
            assert f"{n}. **" in desc

    def test_what_happens_section(self, prompts: PromptManager) -> None:
        desc = _desc(EnterPlanModeTool, prompts)
        assert "What Happens in Plan Mode" in desc

    def test_good_bad_examples(self, prompts: PromptManager) -> None:
        desc = _desc(EnterPlanModeTool, prompts)
        assert "### GOOD" in desc
        assert "### BAD" in desc


# ---------------------------------------------------------------------------
# ExitPlanMode — 3 vim/yank/auth examples
# ---------------------------------------------------------------------------


class TestExitPlanModeAlignment:
    def test_examples_section_present(self, prompts: PromptManager) -> None:
        desc = _desc(ExitPlanModeTool, prompts)
        assert "## Examples" in desc

    def test_three_canonical_examples(self, prompts: PromptManager) -> None:
        desc = _desc(ExitPlanModeTool, prompts)
        assert "vim" in desc        # ex 1 + 2
        assert "yank" in desc       # ex 2
        assert "authentication" in desc  # ex 3


# ---------------------------------------------------------------------------
# ExitWorktree — Behavior section (sans tmux — Mustang has no tmux UX)
# ---------------------------------------------------------------------------


class TestExitWorktreeAlignment:
    def test_behavior_section_present(self, prompts: PromptManager) -> None:
        desc = _desc(ExitWorktreeTool, prompts)
        assert "## Behavior" in desc

    def test_discard_changes_confirmation_guidance(
        self, prompts: PromptManager
    ) -> None:
        """CC teaches the LLM to re-confirm with the user before setting
        discard_changes=true after an error — critical safety guidance."""
        desc = _desc(ExitWorktreeTool, prompts)
        assert "confirm with the user" in desc
        assert "discard_changes" in desc

    def test_no_tmux_mention(self, prompts: PromptManager) -> None:
        """Mustang has no tmux integration — Bash run_in_background uses
        plain asyncio subprocess.  The CC tmux bullet must be dropped so
        the description matches Mustang's real behaviour."""
        desc = _desc(ExitWorktreeTool, prompts)
        assert "tmux" not in desc.lower()


# ---------------------------------------------------------------------------
# EnterWorktree — hooks mention (non-git path)
# ---------------------------------------------------------------------------


class TestEnterWorktreeAlignment:
    def test_hooks_non_git_path_mentioned(self, prompts: PromptManager) -> None:
        """The WorktreeCreate hook fallback path must be advertised so
        the LLM knows non-git projects are supported."""
        desc = _desc(EnterWorktreeTool, prompts)
        assert "WorktreeCreate" in desc or "hook" in desc.lower()
