"""Environment setup check tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from kernel.skills.setup import check_setup
from kernel.skills.types import SkillManifest, SkillSetup, SkillSetupEnvVar


def _manifest(setup: SkillSetup | None = None) -> SkillManifest:
    return SkillManifest(
        name="test",
        description="test",
        has_user_specified_description=True,
        base_dir=Path("/tmp/test"),
        setup=setup,
    )


def test_no_setup_is_ok() -> None:
    ok, msg = check_setup(_manifest())
    assert ok is True
    assert msg is None


def test_all_vars_present() -> None:
    setup = SkillSetup(
        env=(SkillSetupEnvVar(name="MY_VAR", prompt="Enter"),)
    )
    with patch.dict("os.environ", {"MY_VAR": "value"}):
        ok, msg = check_setup(_manifest(setup))
    assert ok is True


def test_required_var_missing() -> None:
    setup = SkillSetup(
        env=(SkillSetupEnvVar(name="MISSING_VAR", prompt="Enter key", help="Get from site"),)
    )
    ok, msg = check_setup(_manifest(setup))
    assert ok is False
    assert "MISSING_VAR" in msg
    assert "MISSING" in msg
    assert "Enter key" in msg
    assert "Get from site" in msg


def test_optional_var_missing_is_ok() -> None:
    setup = SkillSetup(
        env=(SkillSetupEnvVar(name="OPT_VAR", prompt="Optional", optional=True),)
    )
    ok, msg = check_setup(_manifest(setup))
    assert ok is True


def test_optional_with_default_is_ok() -> None:
    setup = SkillSetup(
        env=(
            SkillSetupEnvVar(
                name="OPT_VAR", prompt="Optional", optional=True, default="default_val"
            ),
        )
    )
    ok, msg = check_setup(_manifest(setup))
    assert ok is True


def test_mixed_required_and_optional() -> None:
    setup = SkillSetup(
        env=(
            SkillSetupEnvVar(name="REQUIRED", prompt="Need this"),
            SkillSetupEnvVar(name="OPTIONAL", prompt="Nice to have", optional=True),
        )
    )
    ok, msg = check_setup(_manifest(setup))
    assert ok is False
    assert "REQUIRED" in msg
