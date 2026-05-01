"""Token file persistence tests.

Covers the "what a token file looks like" concerns —
:func:`generate_token` and the load-or-create semantics.  The
underlying atomic-write discipline lives in ``_fs.py`` and is
tested by :mod:`tests.kernel.connection_auth.test_fs`.
"""

from __future__ import annotations

import stat
from pathlib import Path

from kernel.connection_auth.token import generate_token, load_or_create_token


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_generate_token_is_random_and_nonempty() -> None:
    a = generate_token()
    b = generate_token()
    assert a and b
    assert a != b
    # 32 bytes → 43-char unpadded urlsafe base64 (token_urlsafe adds
    # no padding already). Sanity-check the length is in the right
    # ballpark so a future refactor of entropy source is caught.
    assert len(a) >= 40


def test_load_or_create_creates_file_with_0600(tmp_path: Path) -> None:
    path = tmp_path / "state" / "auth_token"
    token = load_or_create_token(path)

    assert path.exists()
    assert path.read_text(encoding="utf-8").strip() == token
    assert _mode(path) == 0o600


def test_load_or_create_is_idempotent(tmp_path: Path) -> None:
    """Second call must return the same token without rewriting."""
    path = tmp_path / "state" / "auth_token"
    first = load_or_create_token(path)
    mtime_after_first = path.stat().st_mtime_ns

    second = load_or_create_token(path)

    assert first == second
    # The file was read, not rewritten — mtime is unchanged.
    assert path.stat().st_mtime_ns == mtime_after_first


def test_load_or_create_regenerates_empty_file(tmp_path: Path) -> None:
    """An empty file means something went wrong; treat as missing."""
    path = tmp_path / "state" / "auth_token"
    path.parent.mkdir(parents=True)
    path.write_text("")

    token = load_or_create_token(path)

    assert token != ""
    assert path.read_text(encoding="utf-8").strip() == token
