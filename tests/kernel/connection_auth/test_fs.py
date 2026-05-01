"""Tests for the shared atomic-write helper.

The helper backs both token and password persistence, so the
security-critical properties (correct mode, atomic replace, stale
tmp cleanup, tmp cleanup on failure) are tested here once rather
than duplicated into both caller-facing modules.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from kernel.connection_auth import _fs as fs_module
from kernel.connection_auth._fs import atomic_write_0600


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_writes_file_with_0600(tmp_path: Path) -> None:
    path = tmp_path / "state" / "secret"
    atomic_write_0600(path, "hello")

    assert path.read_text(encoding="utf-8") == "hello"
    assert _mode(path) == 0o600


def test_creates_parent_dir_with_0700(tmp_path: Path) -> None:
    parent = tmp_path / "fresh-state"
    path = parent / "secret"
    atomic_write_0600(path, "hi")

    assert parent.exists()
    # Only check the "not world / group accessible" bits — some
    # filesystems (tmpfs) mangle higher-order bits in ways we
    # don't want the test to assume.
    assert (_mode(parent) & 0o077) == 0


def test_replaces_existing_atomically(tmp_path: Path) -> None:
    path = tmp_path / "state" / "secret"
    atomic_write_0600(path, "first")
    old_inode = path.stat().st_ino

    atomic_write_0600(path, "second")

    assert path.read_text(encoding="utf-8") == "second"
    assert _mode(path) == 0o600
    # os.replace swaps inodes on POSIX — the new file is a different
    # inode from the one that existed before, which is the whole
    # point of the atomic-replace strategy.
    assert path.stat().st_ino != old_inode


def test_cleans_stale_tmp(tmp_path: Path) -> None:
    """A leftover .tmp from a prior crash must not block rewrite."""
    path = tmp_path / "state" / "secret"
    path.parent.mkdir(parents=True)
    stale = path.with_suffix(path.suffix + ".tmp")
    stale.write_text("leftover")

    atomic_write_0600(path, "fresh")

    assert path.read_text(encoding="utf-8") == "fresh"
    assert not stale.exists()


def test_cleans_tmp_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the write blows up mid-way the ``.tmp`` must not linger."""
    path = tmp_path / "state" / "secret"
    path.parent.mkdir(parents=True)

    def _explode(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(fs_module.os, "fsync", _explode)

    with pytest.raises(OSError, match="disk full"):
        atomic_write_0600(path, "never-lands")

    tmp = path.with_suffix(path.suffix + ".tmp")
    assert not tmp.exists()
    assert not path.exists()
