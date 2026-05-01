"""Disk snapshot cache — write, load, validate."""

from __future__ import annotations

import json
from pathlib import Path

from kernel.skills.snapshot import load_snapshot, validate_snapshot, write_snapshot
from kernel.skills.types import LoadedSkill, SkillManifest, SkillSource


def _make_skill(base: Path, name: str) -> LoadedSkill:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(f"---\nname: {name}\ndescription: test\n---\n# {name}\n")
    return LoadedSkill(
        manifest=SkillManifest(
            name=name,
            description="test",
            has_user_specified_description=True,
            base_dir=skill_dir,
        ),
        source=SkillSource.USER,
        layer_priority=2,
        file_path=skill_md,
    )


def test_write_and_load(tmp_path: Path) -> None:
    skill = _make_skill(tmp_path / "skills", "my-skill")
    snapshot_path = tmp_path / "snapshot.json"

    write_snapshot([skill], [tmp_path / "skills"], snapshot_path=snapshot_path)
    assert snapshot_path.exists()

    entries = load_snapshot(snapshot_path)
    assert entries is not None
    assert len(entries) == 1
    assert entries[0]["name"] == "my-skill"


def test_load_missing_file(tmp_path: Path) -> None:
    result = load_snapshot(tmp_path / "nonexistent.json")
    assert result is None


def test_load_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not json at all")
    result = load_snapshot(path)
    assert result is None


def test_load_version_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"version": 999, "manifest": {}, "skills": []}))
    result = load_snapshot(path)
    assert result is None


def test_validate_snapshot_valid(tmp_path: Path) -> None:
    skill = _make_skill(tmp_path / "skills", "valid")
    snapshot_path = tmp_path / "snapshot.json"
    write_snapshot([skill], [tmp_path / "skills"], snapshot_path=snapshot_path)

    entries = load_snapshot(snapshot_path)
    assert entries is not None
    assert validate_snapshot(entries, [tmp_path / "skills"]) is True


def test_validate_snapshot_stale(tmp_path: Path) -> None:
    skill = _make_skill(tmp_path / "skills", "stale")
    snapshot_path = tmp_path / "snapshot.json"
    write_snapshot([skill], [tmp_path / "skills"], snapshot_path=snapshot_path)

    # Modify the file to make the snapshot stale.
    skill.file_path.write_text("---\nname: stale\ndescription: modified\n---\n# changed\n")

    entries = load_snapshot(snapshot_path)
    assert entries is not None
    assert validate_snapshot(entries, [tmp_path / "skills"]) is False


def test_validate_snapshot_deleted_file(tmp_path: Path) -> None:
    skill = _make_skill(tmp_path / "skills", "gone")
    snapshot_path = tmp_path / "snapshot.json"
    write_snapshot([skill], [tmp_path / "skills"], snapshot_path=snapshot_path)

    # Delete the skill file.
    skill.file_path.unlink()

    entries = load_snapshot(snapshot_path)
    assert entries is not None
    assert validate_snapshot(entries, [tmp_path / "skills"]) is False
