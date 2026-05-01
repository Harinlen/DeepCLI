"""Tests for the skill registry."""

from __future__ import annotations

from pathlib import Path

from daemon.extensions.skills.base import Skill
from daemon.extensions.skills.registry import SkillRegistry


def _make_skill(name: str, path: str = "/fake") -> Skill:
    """Create a minimal Skill for testing."""
    return Skill(
        name=name,
        description=f"Skill {name}",
        source_path=Path(path),
    )


class TestSkillRegistry:
    """Tests for SkillRegistry."""

    def test_register_and_get(self) -> None:
        reg = SkillRegistry()
        skill = _make_skill("alpha")
        assert reg.register(skill) is True
        assert reg.get("alpha") is skill

    def test_get_nonexistent_returns_none(self) -> None:
        reg = SkillRegistry()
        assert reg.get("nope") is None

    def test_duplicate_name_skipped(self) -> None:
        reg = SkillRegistry()
        s1 = _make_skill("dup", "/path/a.md")
        s2 = _make_skill("dup", "/path/b.md")
        assert reg.register(s1) is True
        assert reg.register(s2) is False
        assert len(reg) == 1

    def test_duplicate_path_skipped(self, tmp_path: Path) -> None:
        """Same resolved path with different names is skipped."""
        f = tmp_path / "real.md"
        f.touch()
        s1 = Skill(name="a", description="d", source_path=f)
        s2 = Skill(name="b", description="d", source_path=f)
        reg = SkillRegistry()
        assert reg.register(s1) is True
        assert reg.register(s2) is False

    def test_list_all_sorted(self) -> None:
        reg = SkillRegistry()
        reg.register(_make_skill("charlie", "/c"))
        reg.register(_make_skill("alpha", "/a"))
        reg.register(_make_skill("bravo", "/b"))
        names = [s.name for s in reg.list_all()]
        assert names == ["alpha", "bravo", "charlie"]

    def test_skill_names(self) -> None:
        reg = SkillRegistry()
        reg.register(_make_skill("b", "/b"))
        reg.register(_make_skill("a", "/a"))
        assert reg.skill_names == ["a", "b"]

    def test_contains(self) -> None:
        reg = SkillRegistry()
        reg.register(_make_skill("x", "/x"))
        assert "x" in reg
        assert "y" not in reg

    def test_len(self) -> None:
        reg = SkillRegistry()
        assert len(reg) == 0
        reg.register(_make_skill("a", "/a"))
        assert len(reg) == 1
