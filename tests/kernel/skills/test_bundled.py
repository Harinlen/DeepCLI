"""Bundled skills — registration and extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from kernel.skills.bundled import (
    BundledSkillDef,
    clear_bundled_skills,
    extract_bundled_files,
    get_bundled_skills,
    register_bundled_skill,
)
from kernel.skills.types import SkillSource


def setup_function() -> None:
    clear_bundled_skills()


def test_register_and_get() -> None:
    register_bundled_skill(
        BundledSkillDef(name="test", description="A test", body="Body")
    )
    skills = get_bundled_skills()
    assert len(skills) == 1
    assert skills[0].manifest.name == "test"
    assert skills[0].source == SkillSource.BUNDLED
    assert skills[0].body == "Body"


def test_register_with_files_adds_base_dir_prefix() -> None:
    register_bundled_skill(
        BundledSkillDef(
            name="with-files",
            description="Has files",
            body="Content",
            files={"data.json": '{"key": "value"}'},
        )
    )
    skills = get_bundled_skills()
    assert len(skills) == 1
    assert "Base directory for this skill:" in skills[0].body
    assert "Content" in skills[0].body


def test_clear() -> None:
    register_bundled_skill(
        BundledSkillDef(name="a", description="a", body="a")
    )
    assert len(get_bundled_skills()) == 1
    clear_bundled_skills()
    assert len(get_bundled_skills()) == 0


def test_extract_bundled_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``extract_bundled_files`` writes under ``_BUNDLED_SKILLS_ROOT``,
    # which defaults to ``~/.mustang/bundled-skills/``.  Redirect it
    # to ``tmp_path`` so the test never touches the developer's home.
    from kernel.skills import bundled as bundled_mod

    monkeypatch.setattr(bundled_mod, "_BUNDLED_SKILLS_ROOT", tmp_path)

    result = extract_bundled_files(
        "test-skill",
        {"ref/api.md": "# API", "scripts/run.sh": "#!/bin/sh"},
    )
    assert result is not None
    assert (result / "ref" / "api.md").exists()
    assert (result / "scripts" / "run.sh").exists()
    assert result.is_relative_to(tmp_path)


def test_multiple_registrations() -> None:
    register_bundled_skill(BundledSkillDef(name="a", description="a", body="a"))
    register_bundled_skill(BundledSkillDef(name="b", description="b", body="b"))
    skills = get_bundled_skills()
    assert len(skills) == 2
    names = {s.manifest.name for s in skills}
    assert names == {"a", "b"}
