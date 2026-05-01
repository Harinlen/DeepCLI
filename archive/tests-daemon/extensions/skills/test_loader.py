"""Tests for skill discovery and body loading."""

from __future__ import annotations

import textwrap
from pathlib import Path

from daemon.extensions.skills.loader import discover_skills, load_skill_body

_VALID_SKILL = textwrap.dedent("""\
---
name: "{name}"
description: "Test skill {name}"
---
Body of {name} $ARGUMENTS
""")


def _write_skill(directory: Path, name: str) -> Path:
    """Write a valid skill .md file."""
    f = directory / f"{name}.md"
    f.write_text(_VALID_SKILL.format(name=name))
    return f


class TestDiscoverSkills:
    """Tests for multi-directory skill discovery."""

    def test_discovers_from_single_dir(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "alpha")
        _write_skill(tmp_path, "beta")
        skills = discover_skills([tmp_path])
        names = {s.name for s in skills}
        assert names == {"alpha", "beta"}

    def test_discovers_from_multiple_dirs(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        _write_skill(dir_a, "from_a")
        _write_skill(dir_b, "from_b")
        skills = discover_skills([dir_a, dir_b])
        names = {s.name for s in skills}
        assert names == {"from_a", "from_b"}

    def test_nonexistent_dir_skipped(self) -> None:
        skills = discover_skills([Path("/nonexistent")])
        assert skills == []

    def test_empty_dir(self, tmp_path: Path) -> None:
        skills = discover_skills([tmp_path])
        assert skills == []

    def test_skips_hidden_files(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "visible")
        (tmp_path / ".hidden.md").write_text("---\nname: hidden\ndescription: d\n---\nBody")
        skills = discover_skills([tmp_path])
        assert len(skills) == 1
        assert skills[0].name == "visible"

    def test_skips_invalid_files(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "good")
        (tmp_path / "bad.md").write_text("No frontmatter here")
        skills = discover_skills([tmp_path])
        assert len(skills) == 1
        assert skills[0].name == "good"

    def test_body_not_loaded_at_discovery(self, tmp_path: Path) -> None:
        """Bodies are lazy — not loaded during discovery."""
        _write_skill(tmp_path, "lazy")
        skills = discover_skills([tmp_path])
        assert skills[0].body is None


class TestLoadSkillBody:
    """Tests for on-demand body loading."""

    def test_loads_body(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "test")
        skills = discover_skills([tmp_path])
        skill = skills[0]
        body = load_skill_body(skill)
        assert "Body of test" in body
        assert skill.body is not None

    def test_caches_body(self, tmp_path: Path) -> None:
        """Second call returns cached body without re-reading disk."""
        _write_skill(tmp_path, "cached")
        skills = discover_skills([tmp_path])
        skill = skills[0]
        body1 = load_skill_body(skill)
        body2 = load_skill_body(skill)
        assert body1 == body2

    def test_file_deleted_after_discovery_raises(self, tmp_path: Path) -> None:
        f = _write_skill(tmp_path, "ephemeral")
        skills = discover_skills([tmp_path])
        f.unlink()  # delete after discovery
        import pytest

        with pytest.raises(OSError):
            load_skill_body(skills[0])
