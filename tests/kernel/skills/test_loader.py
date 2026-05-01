"""Skill discovery — multi-layer, dedup, recursive, conditional."""

from __future__ import annotations

from pathlib import Path


from kernel.skills.loader import activate_conditional, discover, discover_for_paths
from kernel.skills.types import SkillSource


def _write_skill(base: Path, name: str, description: str = "test") -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def _write_conditional_skill(
    base: Path, name: str, paths: list[str]
) -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    paths_yaml = "\n".join(f"  - {p}" for p in paths)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test\npaths:\n{paths_yaml}\n---\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


# -- Multi-layer discovery --


def test_discover_project_and_user(tmp_path: Path) -> None:
    proj = tmp_path / "project"
    user = tmp_path / "user"
    _write_skill(proj, "skill-a")
    _write_skill(user, "skill-b")

    unconditional, conditional = discover(
        project_dir=proj,
        project_compat_dir=None,
        external_dirs=[],
        user_dir=user,
        user_compat_dir=None,
        bundled_skills=[],
    )
    names = {s.manifest.name for s in unconditional}
    assert names == {"skill-a", "skill-b"}
    assert conditional == []


def test_project_overrides_user_same_name(tmp_path: Path) -> None:
    proj = tmp_path / "project"
    user = tmp_path / "user"
    _write_skill(proj, "same", description="from project")
    _write_skill(user, "same", description="from user")

    unconditional, _ = discover(
        project_dir=proj,
        project_compat_dir=None,
        external_dirs=[],
        user_dir=user,
        user_compat_dir=None,
        bundled_skills=[],
    )
    assert len(unconditional) == 1
    assert unconditional[0].manifest.description == "from project"
    assert unconditional[0].source == SkillSource.PROJECT


def test_claude_compat_layer(tmp_path: Path) -> None:
    mustang = tmp_path / "mustang"
    claude = tmp_path / "claude"
    _write_skill(mustang, "a")
    _write_skill(claude, "b")

    unconditional, _ = discover(
        project_dir=mustang,
        project_compat_dir=claude,
        external_dirs=[],
        user_dir=tmp_path / "empty",
        user_compat_dir=None,
        bundled_skills=[],
    )
    names = {s.manifest.name for s in unconditional}
    assert "a" in names
    assert "b" in names


def test_mustang_overrides_claude_same_name(tmp_path: Path) -> None:
    mustang = tmp_path / "mustang"
    claude = tmp_path / "claude"
    _write_skill(mustang, "x", description="mustang")
    _write_skill(claude, "x", description="claude")

    unconditional, _ = discover(
        project_dir=mustang,
        project_compat_dir=claude,
        external_dirs=[],
        user_dir=tmp_path / "empty",
        user_compat_dir=None,
        bundled_skills=[],
    )
    assert len(unconditional) == 1
    assert unconditional[0].manifest.description == "mustang"


def test_external_dirs(tmp_path: Path) -> None:
    ext = tmp_path / "team-skills"
    _write_skill(ext, "team-skill")

    unconditional, _ = discover(
        project_dir=None,
        project_compat_dir=None,
        external_dirs=[ext],
        user_dir=tmp_path / "empty",
        user_compat_dir=None,
        bundled_skills=[],
    )
    assert any(s.manifest.name == "team-skill" for s in unconditional)


def test_recursive_scan_hermes_category(tmp_path: Path) -> None:
    """Hermes-style category/skill-name/SKILL.md is discovered."""
    base = tmp_path / "skills"
    _write_skill(base / "devops", "k8s-deploy")
    _write_skill(base / "creative", "image-gen")

    unconditional, _ = discover(
        project_dir=base,
        project_compat_dir=None,
        external_dirs=[],
        user_dir=tmp_path / "empty",
        user_compat_dir=None,
        bundled_skills=[],
    )
    names = {s.manifest.name for s in unconditional}
    assert "k8s-deploy" in names
    assert "image-gen" in names


def test_symlink_dedup(tmp_path: Path) -> None:
    real = tmp_path / "real"
    _write_skill(real, "skill-a")
    link = tmp_path / "link"
    link.symlink_to(real)

    unconditional, _ = discover(
        project_dir=real,
        project_compat_dir=link,
        external_dirs=[],
        user_dir=tmp_path / "empty",
        user_compat_dir=None,
        bundled_skills=[],
    )
    assert len(unconditional) == 1


# -- Conditional skills --


def test_conditional_split(tmp_path: Path) -> None:
    base = tmp_path / "skills"
    _write_skill(base, "normal")
    _write_conditional_skill(base, "cond", ["src/api/**"])

    unconditional, conditional = discover(
        project_dir=base,
        project_compat_dir=None,
        external_dirs=[],
        user_dir=tmp_path / "empty",
        user_compat_dir=None,
        bundled_skills=[],
    )
    assert len(unconditional) == 1
    assert unconditional[0].manifest.name == "normal"
    assert len(conditional) == 1
    assert conditional[0].manifest.name == "cond"


def test_activate_conditional_matching(tmp_path: Path) -> None:
    base = tmp_path / "skills"
    _write_conditional_skill(base, "api-skill", ["src/api/*.py"])

    # Create the file path relative to a project root.
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "src" / "api").mkdir(parents=True)
    file_path = str(project_root / "src" / "api" / "routes.py")

    _, conditional = discover(
        project_dir=base,
        project_compat_dir=None,
        external_dirs=[],
        user_dir=tmp_path / "empty",
        user_compat_dir=None,
        bundled_skills=[],
    )
    pool = {s.manifest.name: s for s in conditional}
    activated = activate_conditional(
        [file_path], str(project_root), pool
    )
    assert len(activated) == 1
    assert activated[0].manifest.name == "api-skill"
    assert "api-skill" not in pool  # Removed from pool.


def test_activate_conditional_no_match(tmp_path: Path) -> None:
    base = tmp_path / "skills"
    _write_conditional_skill(base, "api-skill", ["src/api/*.py"])

    project_root = tmp_path / "project"
    project_root.mkdir()
    file_path = str(project_root / "src" / "web" / "views.py")

    _, conditional = discover(
        project_dir=base,
        project_compat_dir=None,
        external_dirs=[],
        user_dir=tmp_path / "empty",
        user_compat_dir=None,
        bundled_skills=[],
    )
    pool = {s.manifest.name: s for s in conditional}
    activated = activate_conditional(
        [file_path], str(project_root), pool
    )
    assert len(activated) == 0
    assert "api-skill" in pool  # Still in pool.


# -- Dynamic discovery --


def test_discover_for_paths(tmp_path: Path) -> None:
    # Create a nested .mustang/skills/ directory.
    nested = tmp_path / "project" / "packages" / "api" / ".mustang" / "skills"
    nested.mkdir(parents=True)
    _write_skill(nested, "nested-skill")

    cwd = str(tmp_path / "project")
    known: set[str] = set()
    new_dirs = discover_for_paths(
        [str(tmp_path / "project" / "packages" / "api" / "src" / "main.py")],
        cwd,
        known,
    )
    assert len(new_dirs) >= 1
    assert any("nested-skill" in str(d) or ".mustang/skills" in str(d) for d in new_dirs)


def test_discover_for_paths_known_dirs_cached(tmp_path: Path) -> None:
    cwd = str(tmp_path / "project")
    known: set[str] = set()
    # First call.
    discover_for_paths(
        [str(tmp_path / "project" / "sub" / "file.py")],
        cwd,
        known,
    )
    initial_known = len(known)
    # Second call with same file — should not add new dirs.
    discover_for_paths(
        [str(tmp_path / "project" / "sub" / "file.py")],
        cwd,
        known,
    )
    assert len(known) == initial_known


def test_missing_dir_is_fine(tmp_path: Path) -> None:
    """Non-existent directories don't cause errors."""
    unconditional, conditional = discover(
        project_dir=tmp_path / "does-not-exist",
        project_compat_dir=None,
        external_dirs=[],
        user_dir=tmp_path / "also-does-not-exist",
        user_compat_dir=None,
        bundled_skills=[],
    )
    assert unconditional == []
    assert conditional == []
