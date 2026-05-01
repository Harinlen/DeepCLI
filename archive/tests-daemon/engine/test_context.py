"""Tests for system prompt assembly + AGENTS.md / MUSTANG.md discovery."""

from pathlib import Path
from unittest.mock import patch

from daemon.engine.context import (
    PromptSection,
    STATIC_PROMPT,
    build_environment_section,
    build_system_prompt,
    discover_agents_md,
    discover_mustang_md,
    prompt_sections_to_text,
)


class TestStaticPrompt:
    """Verify static prompt sections are present."""

    def test_contains_identity(self) -> None:
        assert "Mustang" in STATIC_PROMPT

    def test_contains_system_section(self) -> None:
        assert "# System" in STATIC_PROMPT

    def test_contains_doing_tasks(self) -> None:
        assert "# Doing tasks" in STATIC_PROMPT

    def test_contains_actions_with_care(self) -> None:
        assert "# Executing actions with care" in STATIC_PROMPT

    def test_contains_using_tools(self) -> None:
        assert "# Using your tools" in STATIC_PROMPT

    def test_contains_tone(self) -> None:
        assert "# Tone and style" in STATIC_PROMPT

    def test_contains_output_efficiency(self) -> None:
        assert "# Output efficiency" in STATIC_PROMPT


class TestDiscoverMustangMd:
    """Tests for MUSTANG.md file discovery."""

    def test_no_mustang_md_found(self, tmp_path: Path) -> None:
        results = discover_mustang_md(tmp_path)
        # May find global ~/.mustang/MUSTANG.md — filter to tmp_path only
        local_results = [(p, c) for p, c in results if str(p).startswith(str(tmp_path))]
        assert local_results == []

    def test_finds_mustang_md_in_cwd(self, tmp_path: Path) -> None:
        md = tmp_path / "MUSTANG.md"
        md.write_text("# Project rules")
        results = discover_mustang_md(tmp_path)
        paths = [p for p, _ in results]
        assert md.resolve() in paths

    def test_finds_mustang_md_in_parent(self, tmp_path: Path) -> None:
        md = tmp_path / "MUSTANG.md"
        md.write_text("# Parent rules")
        child = tmp_path / "subdir"
        child.mkdir()
        results = discover_mustang_md(child)
        paths = [p for p, _ in results]
        assert md.resolve() in paths

    def test_finds_dotdir_mustang_md(self, tmp_path: Path) -> None:
        dotdir = tmp_path / ".mustang"
        dotdir.mkdir()
        md = dotdir / "MUSTANG.md"
        md.write_text("# Dotdir rules")
        results = discover_mustang_md(tmp_path)
        paths = [p for p, _ in results]
        assert md.resolve() in paths

    def test_deepest_first_order(self, tmp_path: Path) -> None:
        """Closest-to-cwd files appear first."""
        parent_md = tmp_path / "MUSTANG.md"
        parent_md.write_text("parent")
        child = tmp_path / "sub"
        child.mkdir()
        child_md = child / "MUSTANG.md"
        child_md.write_text("child")

        results = discover_mustang_md(child)
        # Filter to test paths only
        test_results = [(p, c) for p, c in results if str(p).startswith(str(tmp_path))]
        assert len(test_results) >= 2
        assert test_results[0][1] == "child"
        assert test_results[1][1] == "parent"

    def test_no_duplicates(self, tmp_path: Path) -> None:
        md = tmp_path / "MUSTANG.md"
        md.write_text("once")
        results = discover_mustang_md(tmp_path)
        paths = [p for p, _ in results]
        # Count how many times this specific path appears
        count = sum(1 for p in paths if p == md.resolve())
        assert count == 1


class TestDiscoverAgentsMd:
    """Tests for AGENTS.md discovery (and MUSTANG.md backward-compat)."""

    def test_finds_agents_md_in_cwd(self, tmp_path: Path) -> None:
        md = tmp_path / "AGENTS.md"
        md.write_text("# Agent rules")
        results = discover_agents_md(tmp_path)
        paths = [p for p, _ in results]
        assert md.resolve() in paths

    def test_finds_both_agents_and_mustang(self, tmp_path: Path) -> None:
        """Both names in the same dir → both picked up."""
        (tmp_path / "AGENTS.md").write_text("new name")
        (tmp_path / "MUSTANG.md").write_text("old name")
        results = discover_agents_md(tmp_path)
        test_results = [(p, c) for p, c in results if str(p).startswith(str(tmp_path))]
        contents = [c for _, c in test_results]
        assert "new name" in contents
        assert "old name" in contents

    def test_agents_md_preferred_order(self, tmp_path: Path) -> None:
        """AGENTS.md appears before MUSTANG.md at the same level."""
        (tmp_path / "AGENTS.md").write_text("agents")
        (tmp_path / "MUSTANG.md").write_text("mustang")
        results = discover_agents_md(tmp_path)
        test_results = [(p, c) for p, c in results if str(p).startswith(str(tmp_path))]
        assert test_results[0][1] == "agents"
        assert test_results[1][1] == "mustang"

    def test_finds_dotdir_agents_md(self, tmp_path: Path) -> None:
        dotdir = tmp_path / ".mustang"
        dotdir.mkdir()
        md = dotdir / "AGENTS.md"
        md.write_text("# Dotdir agents")
        results = discover_agents_md(tmp_path)
        paths = [p for p, _ in results]
        assert md.resolve() in paths

    def test_mustang_alias_still_works(self, tmp_path: Path) -> None:
        """Backward-compat: the old function name still resolves."""
        md = tmp_path / "AGENTS.md"
        md.write_text("via alias")
        results = discover_mustang_md(tmp_path)
        paths = [p for p, _ in results]
        assert md.resolve() in paths


class TestBuildEnvironmentSection:
    """Tests for environment info section."""

    def test_contains_cwd(self, tmp_path: Path) -> None:
        section = build_environment_section(tmp_path, "test-model")
        assert str(tmp_path) in section

    def test_contains_model_name(self, tmp_path: Path) -> None:
        section = build_environment_section(tmp_path, "qwen3.5")
        assert "qwen3.5" in section

    def test_contains_platform(self, tmp_path: Path) -> None:
        section = build_environment_section(tmp_path)
        assert "Platform:" in section

    def test_contains_shell(self, tmp_path: Path) -> None:
        section = build_environment_section(tmp_path)
        assert "Shell:" in section


class TestBuildSystemPrompt:
    """Tests for full system prompt assembly."""

    def _text(self, sections: list[PromptSection]) -> str:
        """Convenience: join sections into plain text for assertions."""
        return prompt_sections_to_text(sections)

    def test_returns_prompt_sections(self, tmp_path: Path) -> None:
        sections = build_system_prompt(cwd=tmp_path)
        assert isinstance(sections, list)
        assert all(isinstance(s, PromptSection) for s in sections)

    def test_static_section_is_cacheable(self, tmp_path: Path) -> None:
        sections = build_system_prompt(cwd=tmp_path)
        assert sections[0].cacheable is True
        assert sections[0].text == STATIC_PROMPT

    def test_dynamic_sections_not_cacheable(self, tmp_path: Path) -> None:
        sections = build_system_prompt(cwd=tmp_path)
        # Environment section is the second one — should not be cacheable.
        env_section = sections[1]
        assert env_section.cacheable is False
        assert "# Environment" in env_section.text

    def test_contains_static_prompt(self, tmp_path: Path) -> None:
        prompt = self._text(build_system_prompt(cwd=tmp_path))
        assert STATIC_PROMPT in prompt

    def test_includes_environment(self, tmp_path: Path) -> None:
        prompt = self._text(build_system_prompt(cwd=tmp_path, model_name="qwen3.5"))
        assert "qwen3.5" in prompt
        assert str(tmp_path) in prompt

    def test_includes_mustang_md(self, tmp_path: Path) -> None:
        md = tmp_path / "MUSTANG.md"
        md.write_text("# My project rules")
        prompt = self._text(build_system_prompt(cwd=tmp_path))
        assert "My project rules" in prompt
        assert "OVERRIDE any default behavior" in prompt

    def test_no_mustang_md_section_when_none(self, tmp_path: Path) -> None:
        prompt = self._text(build_system_prompt(cwd=tmp_path))
        assert "OVERRIDE any default behavior" not in prompt

    def test_includes_tool_descriptions(self, tmp_path: Path) -> None:
        tools = "# Tools\n- Bash: run commands"
        prompt = self._text(build_system_prompt(cwd=tmp_path, tool_descriptions=tools))
        assert "Bash: run commands" in prompt

    @patch("daemon.engine.context._detect_git_repo", return_value=True)
    def test_git_repo_detected(self, _mock: object, tmp_path: Path) -> None:
        prompt = self._text(build_system_prompt(cwd=tmp_path))
        assert "Is a git repository: True" in prompt

    def test_git_status_injected(self, tmp_path: Path) -> None:
        """When git_status is provided, a # Git Context section appears."""
        git_block = "Current branch: main\nStatus:\n(clean)"
        prompt = self._text(build_system_prompt(cwd=tmp_path, git_status=git_block))
        assert "# Git Context" in prompt
        assert "Current branch: main" in prompt

    def test_git_status_none_omits_section(self, tmp_path: Path) -> None:
        """git_status=None omits the section entirely."""
        prompt = self._text(build_system_prompt(cwd=tmp_path, git_status=None))
        assert "# Git Context" not in prompt

    def test_git_status_placed_before_mustang_md(self, tmp_path: Path) -> None:
        """Git Context comes after environment, before MUSTANG.md."""
        (tmp_path / "MUSTANG.md").write_text("# rules", encoding="utf-8")
        prompt = self._text(build_system_prompt(cwd=tmp_path, git_status="Current branch: xyz"))
        env_pos = prompt.find("# Environment")
        git_pos = prompt.find("# Git Context")
        md_pos = prompt.find("OVERRIDE any default behavior")
        assert env_pos != -1 and git_pos != -1 and md_pos != -1
        assert env_pos < git_pos < md_pos


class TestPlanMode:
    """Plan-mode prompt injection (Step 4.8)."""

    def _text(self, sections: list[PromptSection]) -> str:
        return prompt_sections_to_text(sections)

    def test_plan_mode_inactive_omits_section(self, tmp_path: Path) -> None:
        """Without ``plan_mode=True`` no plan instructions appear."""
        prompt = self._text(build_system_prompt(cwd=tmp_path, plan_mode=False))
        assert "Plan mode is ACTIVE" not in prompt
        assert "Plan mode still active" not in prompt

    def test_plan_mode_first_turn_injects_full_instructions(self, tmp_path: Path) -> None:
        """First turn in plan mode gets the long-form instructions."""
        prompt = self._text(
            build_system_prompt(cwd=tmp_path, plan_mode=True, plan_mode_first_turn=True)
        )
        assert "Plan mode is ACTIVE" in prompt
        assert "exit_plan_mode" in prompt

    def test_plan_mode_subsequent_turn_injects_reminder(self, tmp_path: Path) -> None:
        """Subsequent turns get the short reminder."""
        prompt = self._text(
            build_system_prompt(cwd=tmp_path, plan_mode=True, plan_mode_first_turn=False)
        )
        assert "Plan mode still active" in prompt
        assert "Plan mode is ACTIVE" not in prompt
