"""Tests for skill base model and parsing."""

from __future__ import annotations

import textwrap
from pathlib import Path

from daemon.extensions.skills.base import (
    Skill,
    _split_frontmatter,
    parse_skill_file,
    render_skill_body,
)


class TestSplitFrontmatter:
    """Tests for frontmatter/body splitting."""

    def test_valid_frontmatter(self) -> None:
        text = "---\nname: test\n---\nBody here"
        fm, body = _split_frontmatter(text)
        assert fm == "name: test"
        assert body == "Body here"

    def test_no_frontmatter(self) -> None:
        text = "Just a body with no frontmatter"
        fm, body = _split_frontmatter(text)
        assert fm is None
        assert body == text

    def test_missing_closing_delimiter(self) -> None:
        text = "---\nname: test\nNo closing"
        fm, body = _split_frontmatter(text)
        assert fm is None

    def test_empty_body(self) -> None:
        text = "---\nname: test\n---\n"
        fm, body = _split_frontmatter(text)
        assert fm == "name: test"
        assert body == ""

    def test_multiline_frontmatter(self) -> None:
        text = "---\nname: test\ndescription: A desc\nmodel: qwen\n---\nBody"
        fm, body = _split_frontmatter(text)
        assert "name: test" in fm
        assert "model: qwen" in fm
        assert body == "Body"

    def test_body_with_dashes(self) -> None:
        """Dashes in body (not at line start) don't break parsing."""
        text = "---\nname: test\n---\nSome text with --- in it"
        fm, body = _split_frontmatter(text)
        assert fm == "name: test"
        assert "---" in body


class TestParseSkillFile:
    """Tests for parsing skill .md files."""

    def test_valid_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text(
            textwrap.dedent("""\
            ---
            name: "commit"
            description: "Create a git commit"
            whenToUse: "When code changes are done"
            model: "qwen3.5"
            arguments: "message:string"
            ---
            Commit body here $ARGUMENTS
            """)
        )
        skill = parse_skill_file(f)
        assert skill is not None
        assert skill.name == "commit"
        assert skill.description == "Create a git commit"
        assert skill.when_to_use == "When code changes are done"
        assert skill.model == "qwen3.5"
        assert skill.arguments == "message:string"
        assert skill.body is None  # lazy

    def test_minimal_frontmatter(self, tmp_path: Path) -> None:
        """Only name and description are required."""
        f = tmp_path / "minimal.md"
        f.write_text("---\nname: min\ndescription: Minimal\n---\nBody")
        skill = parse_skill_file(f)
        assert skill is not None
        assert skill.name == "min"
        assert skill.when_to_use is None
        assert skill.model is None
        assert skill.arguments is None

    def test_missing_name_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "no_name.md"
        f.write_text("---\ndescription: No name\n---\nBody")
        assert parse_skill_file(f) is None

    def test_missing_description_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "no_desc.md"
        f.write_text("---\nname: test\n---\nBody")
        assert parse_skill_file(f) is None

    def test_no_frontmatter_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "plain.md"
        f.write_text("Just plain markdown")
        assert parse_skill_file(f) is None

    def test_invalid_yaml_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.md"
        f.write_text("---\n: : invalid\n---\nBody")
        assert parse_skill_file(f) is None

    def test_nonexistent_file_returns_none(self) -> None:
        assert parse_skill_file(Path("/nonexistent/skill.md")) is None

    def test_source_path_resolved(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("---\nname: t\ndescription: d\n---\nBody")
        skill = parse_skill_file(f)
        assert skill is not None
        assert skill.source_path.is_absolute()


class TestRenderSkillBody:
    """Tests for argument substitution in skill body."""

    def test_basic_substitution(self) -> None:
        skill = Skill(
            name="t",
            description="d",
            source_path=Path("/fake"),
            body="Do $ARGUMENTS now",
        )
        result = render_skill_body(skill, "fix the bug")
        assert result == "Do fix the bug now"

    def test_no_arguments_placeholder(self) -> None:
        skill = Skill(
            name="t",
            description="d",
            source_path=Path("/fake"),
            body="No placeholder here",
        )
        result = render_skill_body(skill, "ignored")
        assert result == "No placeholder here"

    def test_empty_arguments(self) -> None:
        skill = Skill(
            name="t",
            description="d",
            source_path=Path("/fake"),
            body="Do $ARGUMENTS now",
        )
        result = render_skill_body(skill)
        assert result == "Do  now"

    def test_body_not_loaded_raises(self) -> None:
        skill = Skill(name="t", description="d", source_path=Path("/fake"))
        import pytest

        with pytest.raises(ValueError, match="not loaded"):
            render_skill_body(skill)
