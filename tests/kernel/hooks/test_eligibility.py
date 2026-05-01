"""Eligibility filter — OS / bins / env predicates."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kernel.hooks.eligibility import is_eligible
from kernel.hooks.manifest import HookManifest, HookRequires


def _make(
    *,
    os_list: tuple[str, ...] = (),
    bins: tuple[str, ...] = (),
    env: tuple[str, ...] = (),
) -> HookManifest:
    return HookManifest(
        name="test",
        description="",
        events=("stop",),
        requires=HookRequires(bins=bins, env=env),
        os=os_list,
        base_dir=Path("/tmp"),
        handler_path=Path("/tmp/handler.py"),
    )


def test_no_requirements_passes() -> None:
    eligible, reason = is_eligible(_make())
    assert eligible is True
    assert reason is None


def test_os_allow_list_matches_current() -> None:
    eligible, reason = is_eligible(_make(os_list=(sys.platform,)))
    assert eligible is True
    assert reason is None


def test_os_allow_list_excludes_current() -> None:
    eligible, reason = is_eligible(_make(os_list=("plan9-and-friends",)))
    assert eligible is False
    assert reason is not None
    assert sys.platform in reason


def test_required_binary_present() -> None:
    """``sh`` is on every POSIX PATH; on Windows the test still
    exercises the success branch via ``cmd``."""
    binary = "sh" if sys.platform != "win32" else "cmd"
    eligible, _ = is_eligible(_make(bins=(binary,)))
    assert eligible is True


def test_required_binary_absent() -> None:
    eligible, reason = is_eligible(_make(bins=("definitely-not-on-path-xyz123",)))
    assert eligible is False
    assert reason is not None
    assert "definitely-not-on-path-xyz123" in reason


def test_required_env_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSTANG_TEST_HOOK_ENV", "1")
    eligible, _ = is_eligible(_make(env=("MUSTANG_TEST_HOOK_ENV",)))
    assert eligible is True


def test_required_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MUSTANG_TEST_HOOK_ENV", raising=False)
    eligible, reason = is_eligible(_make(env=("MUSTANG_TEST_HOOK_ENV",)))
    assert eligible is False
    assert reason is not None
    assert "MUSTANG_TEST_HOOK_ENV" in reason


def test_required_env_empty_string_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUSTANG_TEST_HOOK_ENV", "")
    eligible, _ = is_eligible(_make(env=("MUSTANG_TEST_HOOK_ENV",)))
    assert eligible is False
