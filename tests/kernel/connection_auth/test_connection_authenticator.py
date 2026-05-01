"""End-to-end tests for :class:`kernel.connection_auth.ConnectionAuthenticator`.

Wired up against a real :class:`kernel.module_table.KernelModuleTable`
so the same integration surface subsystems use at runtime is
exercised here — mocking the module table would hide binding errors
between ConnectionAuthenticator, ConfigManager, and the state
directory.

The module table is synthesized per-test:

- ``FlagManager`` pointed at a tmp ``flags.yaml`` so it never reads
  real user state
- ``ConfigManager`` pointed at tmp global / project dirs and an
  empty ``environ`` so there are no surprise env overrides
- ``state_dir`` is a fresh tmp subdirectory

No fixture is shared yet because this is the first subsystem needing
a module table; when the second one appears we can pull the builder
out to ``tests/kernel/conftest.py``.
"""

from __future__ import annotations

import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pytest

from kernel.connection_auth import AuthContext, AuthError, ConnectionAuthenticator
from kernel.connection_auth.password import hash_password
from kernel.config import ConfigManager
from kernel.flags import FlagManager
from kernel.module_table import KernelModuleTable


# --------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------


@pytest.fixture
async def module_table(tmp_path: Path) -> KernelModuleTable:
    """Build a live module table rooted entirely in ``tmp_path``.

    Each sub-path is created up front so
    ConnectionAuthenticator's ``startup`` only has to ``mkdir``
    the state dir (which is already present) and bind its config
    section.
    """
    global_dir = tmp_path / "config"
    project_dir = tmp_path / "project-config"
    state_dir = tmp_path / "state"
    global_dir.mkdir()
    project_dir.mkdir()
    state_dir.mkdir(mode=0o700)

    flags = FlagManager(path=tmp_path / "flags.yaml")
    await flags.initialize()

    config = ConfigManager(
        global_dir=global_dir,
        project_dir=project_dir,
        cli_overrides=(),
    )
    await config.startup()

    return KernelModuleTable(flags=flags, config=config, state_dir=state_dir)


@pytest.fixture
async def auth(module_table: KernelModuleTable) -> ConnectionAuthenticator:
    """A started ConnectionAuthenticator bound to the test module table."""
    manager = ConnectionAuthenticator(module_table)
    await manager.startup()
    return manager


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------


async def test_startup_creates_token_file_0600(
    module_table: KernelModuleTable,
) -> None:
    manager = ConnectionAuthenticator(module_table)
    await manager.startup()

    token_path = module_table.state_dir / "auth_token"
    assert token_path.exists()
    assert _mode(token_path) == 0o600
    assert manager.has_password() is False


async def test_startup_reads_existing_token(
    module_table: KernelModuleTable,
) -> None:
    """A pre-existing token file must survive startup unchanged."""
    token_path = module_table.state_dir / "auth_token"
    token_path.write_text("preexisting-token-value")

    manager = ConnectionAuthenticator(module_table)
    await manager.startup()

    # The manager now has this token cached; the easy way to assert
    # that is to verify authentication with it.
    ctx = await manager.authenticate(
        connection_id="c1",
        credential="preexisting-token-value",
        credential_type="token",
        remote_addr="127.0.0.1:1",
    )
    assert ctx.credential_type == "token"


async def test_startup_with_password_hash_present(
    module_table: KernelModuleTable,
) -> None:
    """Pre-written password hash must be picked up on startup."""
    pw_path = module_table.state_dir / "auth_password.hash"
    pw_path.write_text(hash_password("rem0te"))

    manager = ConnectionAuthenticator(module_table)
    await manager.startup()

    assert manager.has_password() is True


# --------------------------------------------------------------------
# authenticate()
# --------------------------------------------------------------------


async def test_authenticate_token_success(auth: ConnectionAuthenticator) -> None:
    token_path = auth._token_path
    assert token_path is not None
    token = token_path.read_text(encoding="utf-8").strip()

    ctx = await auth.authenticate(
        connection_id="conn-a",
        credential=token,
        credential_type="token",
        remote_addr="127.0.0.1:54321",
    )

    assert isinstance(ctx, AuthContext)
    assert ctx.connection_id == "conn-a"
    assert ctx.credential_type == "token"
    assert ctx.remote_addr == "127.0.0.1:54321"
    assert ctx.is_local is True
    assert isinstance(ctx.authenticated_at, datetime)
    assert ctx.authenticated_at.tzinfo is timezone.utc


async def test_authenticate_token_wrong_raises(auth: ConnectionAuthenticator) -> None:
    with pytest.raises(AuthError) as exc_info:
        await auth.authenticate(
            connection_id="conn-b",
            credential="not-the-real-token",
            credential_type="token",
            remote_addr="127.0.0.1:1",
        )
    # Fixed message — never leak which part failed.
    assert "authentication failed" in str(exc_info.value)
    assert "not-the-real-token" not in str(exc_info.value)


async def test_authenticate_password_disabled_rejects(
    auth: ConnectionAuthenticator,
) -> None:
    """A password credential on a kernel with no password set fails."""
    assert auth.has_password() is False
    with pytest.raises(AuthError):
        await auth.authenticate(
            connection_id="conn-c",
            credential="anything",
            credential_type="password",
            remote_addr="127.0.0.1:1",
        )


async def test_authenticate_password_success_is_not_local(
    auth: ConnectionAuthenticator,
) -> None:
    auth.set_password("correct-horse")

    ctx = await auth.authenticate(
        connection_id="conn-d",
        credential="correct-horse",
        credential_type="password",
        remote_addr="127.0.0.1:44444",
    )

    assert ctx.credential_type == "password"
    assert ctx.is_local is False  # password is never "definitely local"


async def test_authenticate_password_wrong_raises(auth: ConnectionAuthenticator) -> None:
    auth.set_password("correct-horse")
    with pytest.raises(AuthError):
        await auth.authenticate(
            connection_id="conn-e",
            credential="WRONG",
            credential_type="password",
            remote_addr="127.0.0.1:1",
        )


async def test_authenticate_unknown_credential_type_raises(
    auth: ConnectionAuthenticator,
) -> None:
    """Transports are untrusted input — unknown type ⇒ fail, not crash."""
    with pytest.raises(AuthError):
        await auth.authenticate(
            connection_id="conn-f",
            credential="irrelevant",
            credential_type="cookie",  # type: ignore[arg-type]
            remote_addr="127.0.0.1:1",
        )


# --------------------------------------------------------------------
# Token rotation
# --------------------------------------------------------------------


async def test_rotate_token_invalidates_old_token(
    auth: ConnectionAuthenticator,
) -> None:
    token_path = auth._token_path
    assert token_path is not None
    old = token_path.read_text(encoding="utf-8").strip()

    auth.rotate_token()
    new = token_path.read_text(encoding="utf-8").strip()

    assert new != old
    assert _mode(token_path) == 0o600

    # Old token must no longer authenticate.
    with pytest.raises(AuthError):
        await auth.authenticate(
            connection_id="c",
            credential=old,
            credential_type="token",
            remote_addr="127.0.0.1:1",
        )
    # New token works.
    ctx = await auth.authenticate(
        connection_id="c",
        credential=new,
        credential_type="token",
        remote_addr="127.0.0.1:1",
    )
    assert ctx.credential_type == "token"


# --------------------------------------------------------------------
# Password set / clear
# --------------------------------------------------------------------


async def test_set_password_persists_and_enables(auth: ConnectionAuthenticator) -> None:
    pw_path = auth._password_path
    assert pw_path is not None
    assert not pw_path.exists()

    auth.set_password("remote-secret")

    assert auth.has_password() is True
    assert pw_path.exists()
    assert _mode(pw_path) == 0o600
    # The file contains a hash, not the plaintext.
    stored = pw_path.read_text(encoding="utf-8").strip()
    assert "remote-secret" not in stored
    assert stored.startswith("scrypt$")


async def test_clear_password_disables(auth: ConnectionAuthenticator) -> None:
    auth.set_password("remote-secret")
    pw_path = auth._password_path
    assert pw_path is not None
    assert pw_path.exists()

    auth.clear_password()

    assert auth.has_password() is False
    assert not pw_path.exists()


async def test_clear_password_is_idempotent(auth: ConnectionAuthenticator) -> None:
    """Safe to clear when already cleared."""
    assert auth.has_password() is False
    auth.clear_password()  # must not raise
    assert auth.has_password() is False


async def test_set_password_overwrites_previous(auth: ConnectionAuthenticator) -> None:
    auth.set_password("first")
    auth.set_password("second")

    # Old password no longer works; new one does.
    ctx = await auth.authenticate(
        connection_id="c",
        credential="second",
        credential_type="password",
        remote_addr="127.0.0.1:1",
    )
    assert ctx.credential_type == "password"
    with pytest.raises(AuthError):
        await auth.authenticate(
            connection_id="c",
            credential="first",
            credential_type="password",
            remote_addr="127.0.0.1:1",
        )


# --------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------


async def test_construction_does_no_io(
    module_table: KernelModuleTable,
) -> None:
    """__init__ resolves paths but must not touch disk.

    This matters for the ``Subsystem.load`` contract: the constructor
    runs before ``startup``, and any I/O there would escape the
    lifespan's error-handling try/except around ``startup``.
    """
    ConnectionAuthenticator(module_table)
    assert not (module_table.state_dir / "auth_token").exists()
    assert not (module_table.state_dir / "auth_password.hash").exists()


# --------------------------------------------------------------------
# AuthContext properties
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("credential_type", "expected_local"),
    [("token", True), ("password", False)],
)
def test_auth_context_is_local_matches_credential_type(
    credential_type: Literal["token", "password"],
    expected_local: bool,
) -> None:
    ctx = AuthContext(
        connection_id="x",
        credential_type=credential_type,
        remote_addr="127.0.0.1:1",
        authenticated_at=datetime.now(timezone.utc),
    )
    assert ctx.is_local is expected_local


def test_auth_context_is_frozen() -> None:
    ctx = AuthContext(
        connection_id="x",
        credential_type="token",
        remote_addr="127.0.0.1:1",
        authenticated_at=datetime.now(timezone.utc),
    )
    with pytest.raises(Exception):  # FrozenInstanceError subclass of AttributeError
        ctx.connection_id = "y"  # type: ignore[misc]
