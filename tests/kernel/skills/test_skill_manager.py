"""SkillManager — startup, listing, activation, dynamic discovery."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kernel.skills import SkillManager


def _write_skill(base: Path, name: str, description: str = "test", **extras) -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    extra_yaml = "\n".join(f"{k}: {v}" for k, v in extras.items())
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra_yaml}\n---\n"
        f"# {name}\n\nSkill body for {name}.\n\n"
        f"Use $ARGUMENTS here.\n",
        encoding="utf-8",
    )
    return skill_dir


def _make_module_table() -> MagicMock:
    mt = MagicMock()
    mt.config = MagicMock()
    mt.config.bind_section = MagicMock(return_value=MagicMock())
    return mt


@pytest.fixture
def skills_dir(tmp_path: Path) -> tuple[Path, Path]:
    """Create project + user skill directories."""
    project = tmp_path / "project"
    user = tmp_path / "user"
    project.mkdir()
    user.mkdir()
    return project, user


@pytest.fixture
def manager(skills_dir: tuple[Path, Path]) -> SkillManager:
    project, user = skills_dir
    mt = _make_module_table()
    return SkillManager(
        mt,
        user_skills_dir=user,
        project_skills_dir=project,
    )


# -- Startup --


@pytest.mark.asyncio
async def test_startup_discovers_skills(
    skills_dir: tuple[Path, Path], manager: SkillManager
) -> None:
    project, user = skills_dir
    _write_skill(project, "proj-skill")
    _write_skill(user, "user-skill")

    await manager.startup()
    listing = manager.get_skill_listing()
    assert "proj-skill" in listing
    assert "user-skill" in listing


@pytest.mark.asyncio
async def test_startup_empty_dirs(manager: SkillManager) -> None:
    await manager.startup()
    assert manager.get_skill_listing() == ""


# -- Listing --


@pytest.mark.asyncio
async def test_listing_includes_when_to_use(
    skills_dir: tuple[Path, Path], manager: SkillManager
) -> None:
    project, _ = skills_dir
    _write_skill(project, "my-skill", when_to_use="When testing")
    await manager.startup()
    listing = manager.get_skill_listing()
    assert "When testing" in listing


@pytest.mark.asyncio
async def test_listing_excludes_disabled(
    skills_dir: tuple[Path, Path], manager: SkillManager
) -> None:
    project, _ = skills_dir
    _write_skill(project, "hidden", **{"disable-model-invocation": "true"})
    _write_skill(project, "visible")
    await manager.startup()
    listing = manager.get_skill_listing()
    assert "visible" in listing
    assert "hidden" not in listing


# -- Activation --


@pytest.mark.asyncio
async def test_activate_returns_body(
    skills_dir: tuple[Path, Path], manager: SkillManager
) -> None:
    project, _ = skills_dir
    _write_skill(project, "test-skill")
    await manager.startup()

    result = manager.activate("test-skill", args="hello")
    assert result is not None
    assert "hello" in result.body  # $ARGUMENTS replaced


@pytest.mark.asyncio
async def test_activate_unknown_returns_none(manager: SkillManager) -> None:
    await manager.startup()
    assert manager.activate("nonexistent") is None


@pytest.mark.asyncio
async def test_activate_tracks_invoked(
    skills_dir: tuple[Path, Path], manager: SkillManager
) -> None:
    project, _ = skills_dir
    _write_skill(project, "tracked")
    await manager.startup()

    manager.activate("tracked")
    invoked = manager.get_invoked_for_agent()
    assert len(invoked) == 1
    assert invoked[0].skill_name == "tracked"


@pytest.mark.asyncio
async def test_activate_supporting_files(
    skills_dir: tuple[Path, Path], manager: SkillManager
) -> None:
    project, _ = skills_dir
    skill_dir = _write_skill(project, "with-refs")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "api.md").write_text("API docs")
    await manager.startup()

    result = manager.activate("with-refs")
    assert result is not None
    assert "references/api.md" in result.body


# -- Invoked tracking --


@pytest.mark.asyncio
async def test_clear_invoked(
    skills_dir: tuple[Path, Path], manager: SkillManager
) -> None:
    project, _ = skills_dir
    _write_skill(project, "s1")
    await manager.startup()
    manager.activate("s1")
    assert len(manager.get_invoked_for_agent()) == 1

    manager.clear_invoked()
    assert len(manager.get_invoked_for_agent()) == 0


# -- Lookup --


@pytest.mark.asyncio
async def test_lookup(
    skills_dir: tuple[Path, Path], manager: SkillManager
) -> None:
    project, _ = skills_dir
    _write_skill(project, "findme")
    await manager.startup()
    assert manager.lookup("findme") is not None
    assert manager.lookup("nope") is None


@pytest.mark.asyncio
async def test_user_invocable_skills(
    skills_dir: tuple[Path, Path], manager: SkillManager
) -> None:
    project, _ = skills_dir
    _write_skill(project, "public")
    _write_skill(project, "private", **{"user-invocable": "false"})
    await manager.startup()
    names = {s.manifest.name for s in manager.user_invocable_skills()}
    assert "public" in names
    assert "private" not in names
