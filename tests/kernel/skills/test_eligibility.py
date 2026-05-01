"""Eligibility checks — static (startup) + dynamic (listing)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


from kernel.skills.eligibility import is_eligible, is_visible
from kernel.skills.types import (
    LoadedSkill,
    SkillFallbackFor,
    SkillManifest,
    SkillRequires,
    SkillSource,
)


def _manifest(**overrides) -> SkillManifest:
    defaults = dict(
        name="test",
        description="test skill",
        has_user_specified_description=True,
        base_dir=Path("/tmp/test"),
    )
    defaults.update(overrides)
    return SkillManifest(**defaults)


def _skill(manifest: SkillManifest | None = None, **overrides) -> LoadedSkill:
    m = manifest or _manifest(**overrides)
    return LoadedSkill(
        manifest=m,
        source=SkillSource.USER,
        layer_priority=2,
        file_path=Path("/tmp/test/SKILL.md"),
    )


# -- Static eligibility --


def test_eligible_no_requirements() -> None:
    ok, reason = is_eligible(_manifest())
    assert ok is True
    assert reason is None


def test_os_mismatch() -> None:
    # Use an OS that doesn't match the current platform.
    fake_os = "win32" if sys.platform != "win32" else "darwin"
    ok, reason = is_eligible(_manifest(os=(fake_os,)))
    assert ok is False
    assert "not in allow-list" in reason


def test_os_match() -> None:
    ok, _ = is_eligible(_manifest(os=(sys.platform,)))
    assert ok is True


def test_missing_binary() -> None:
    ok, reason = is_eligible(
        _manifest(requires=SkillRequires(bins=("definitely_not_a_real_binary_xyz",)))
    )
    assert ok is False
    assert "not on PATH" in reason


def test_present_binary() -> None:
    ok, _ = is_eligible(_manifest(requires=SkillRequires(bins=("python3",))))
    assert ok is True


def test_missing_env_var() -> None:
    ok, reason = is_eligible(
        _manifest(requires=SkillRequires(env=("DEFINITELY_NOT_SET_XYZ_123",)))
    )
    assert ok is False
    assert "unset or empty" in reason


def test_present_env_var() -> None:
    with patch.dict("os.environ", {"MY_TEST_VAR": "value"}):
        ok, _ = is_eligible(
            _manifest(requires=SkillRequires(env=("MY_TEST_VAR",)))
        )
    assert ok is True


# -- Dynamic visibility --


def test_visible_no_requirements() -> None:
    assert is_visible(_skill(), set()) is True


def test_requires_tools_missing() -> None:
    skill = _skill(manifest=_manifest(requires=SkillRequires(tools=("WebSearch",))))
    assert is_visible(skill, {"Bash", "Grep"}) is False


def test_requires_tools_present() -> None:
    skill = _skill(manifest=_manifest(requires=SkillRequires(tools=("Bash",))))
    assert is_visible(skill, {"Bash", "Grep"}) is True


def test_fallback_for_hides_when_primary_available() -> None:
    skill = _skill(
        manifest=_manifest(fallback_for=SkillFallbackFor(tools=("WebSearch",)))
    )
    # Primary is available → fallback should be hidden.
    assert is_visible(skill, {"WebSearch", "Bash"}) is False


def test_fallback_for_visible_when_primary_missing() -> None:
    skill = _skill(
        manifest=_manifest(fallback_for=SkillFallbackFor(tools=("WebSearch",)))
    )
    # Primary is missing → fallback should be visible.
    assert is_visible(skill, {"Bash", "Grep"}) is True
