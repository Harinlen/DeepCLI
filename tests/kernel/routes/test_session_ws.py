"""End-to-end tests for the ``/session`` WebSocket transport layer.

These exercise the real FastAPI lifespan via
:class:`fastapi.testclient.TestClient`.  Every subsystem except
ConnectionAuthenticator is a placeholder stub (see ``kernel/*/__init__.py``),
and ConnectionAuthenticator itself is redirected at a tmp state dir by
patching ``Path.home`` before any lifespan-owned code reads it.

The transport happens to echo messages today because the live
protocol stack is the dummy pass-through — these tests therefore
cover both the transport layer's close-code behavior and the
dummy stack's identity semantics in one place.  When the real ACP
stack lands, most of the authentication assertions will still be
valid; only the "echo after auth" check will change.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kernel import app as app_module
from kernel.connection_auth.password import hash_password
from kernel.config import ConfigManager
from kernel.flags import FlagManager

_CLOSE_AUTH_FAILED = 4003
_CLOSE_INTERNAL_ERROR = 1011


@pytest.fixture
def mustang_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect everything the lifespan touches into ``tmp_path``.

    Three redirections are needed and they happen at **different
    times**:

    - :class:`FlagManager` and :class:`ConfigManager` both compute
      their default user-home paths at *module import time*, so
      patching ``Path.home`` after import is useless — the module
      already captured the real user path.  We shim the factories
      the lifespan uses in :mod:`kernel.app` so they receive
      explicit tmp paths.
    - :class:`ConnectionAuthenticator` reads its state dir from the module
      table at *runtime* inside the lifespan, and the lifespan
      computes that dir with a fresh ``Path.home()`` call.  For
      that one we still need the monkeypatch.

    With all three redirected the test run can never touch the
    developer's real ``~/.mustang`` tree even accidentally.
    """
    global_dir = tmp_path / ".mustang" / "config"
    project_dir = tmp_path / "project-config"
    flags_path = tmp_path / ".mustang" / "flags.yaml"
    global_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(app_module, "FlagManager", lambda: FlagManager(path=flags_path))

    from kernel.secrets import SecretManager

    monkeypatch.setattr(
        app_module, "SecretManager",
        lambda: SecretManager(db_path=tmp_path / ".mustang" / "secrets.db"),
    )
    monkeypatch.setattr(
        app_module,
        "ConfigManager",
        lambda **kwargs: ConfigManager(
            global_dir=global_dir,
            project_dir=project_dir,
            cli_overrides=(),
            **kwargs,
        ),
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def client(mustang_home: Path) -> TestClient:
    """Build the real app and return a TestClient that drives its lifespan."""
    app = app_module.create_app()
    return TestClient(app)


def _read_token(mustang_home: Path) -> str:
    """Return the token ConnectionAuthenticator created during startup."""
    token_path = mustang_home / ".mustang" / "state" / "auth_token"
    return token_path.read_text(encoding="utf-8").strip()


def _install_password(mustang_home: Path, plaintext: str) -> None:
    """Pre-seed the password hash so ConnectionAuthenticator picks it up on startup."""
    state_dir = mustang_home / ".mustang" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "auth_password.hash").write_text(hash_password(plaintext), encoding="utf-8")


def _assert_close_code(ctx_manager, expected: int) -> None:
    """Enter ``ctx_manager``, expect an immediate disconnect with ``expected``.

    ``TestClient.websocket_connect`` raises
    :class:`WebSocketDisconnect` if the server closes during
    handshake; otherwise we have to ``receive`` inside the
    context to observe the close.  Either path produces the same
    exception, so we try to read one frame and let whatever
    happens first determine the assertion.
    """
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with ctx_manager as ws:
            ws.receive_text()
    assert exc_info.value.code == expected


# --------------------------------------------------------------------
# Missing / ambiguous credentials
# --------------------------------------------------------------------


def test_no_credentials_closes_with_4003(client: TestClient) -> None:
    with client:
        _assert_close_code(client.websocket_connect("/session"), _CLOSE_AUTH_FAILED)


def test_empty_token_closes_with_4003(client: TestClient) -> None:
    """Empty-string credential is treated the same as missing."""
    with client:
        _assert_close_code(
            client.websocket_connect("/session?token="),
            _CLOSE_AUTH_FAILED,
        )


# --------------------------------------------------------------------
# Token auth
# --------------------------------------------------------------------


def test_wrong_token_closes_with_4003(client: TestClient, mustang_home: Path) -> None:
    # Triggering lifespan once so ConnectionAuthenticator creates the real token file.
    with client:
        real = _read_token(mustang_home)
        assert real  # sanity

        _assert_close_code(
            client.websocket_connect("/session?token=not-the-real-one"),
            _CLOSE_AUTH_FAILED,
        )


def test_correct_token_connects_and_echoes(client: TestClient, mustang_home: Path) -> None:
    with client:
        token = _read_token(mustang_home)

        with client.websocket_connect(f"/session?token={token}") as ws:
            ws.send_text("hello")
            assert ws.receive_text() == "hello"

            ws.send_text('{"foo": 1}')
            assert ws.receive_text() == '{"foo": 1}'


# --------------------------------------------------------------------
# Password auth
# --------------------------------------------------------------------


def test_password_without_hash_closes_with_4003(
    client: TestClient,
) -> None:
    """Password credential on a kernel without a password set → reject."""
    with client:
        _assert_close_code(
            client.websocket_connect("/session?password=whatever"),
            _CLOSE_AUTH_FAILED,
        )


def test_wrong_password_closes_with_4003(
    mustang_home: Path,
) -> None:
    _install_password(mustang_home, "correct-horse")

    app = app_module.create_app()
    client = TestClient(app)
    with client:
        _assert_close_code(
            client.websocket_connect("/session?password=wrong"),
            _CLOSE_AUTH_FAILED,
        )


def test_correct_password_connects_and_echoes(
    mustang_home: Path,
) -> None:
    _install_password(mustang_home, "correct-horse")

    app = app_module.create_app()
    client = TestClient(app)
    with client:
        with client.websocket_connect("/session?password=correct-horse") as ws:
            ws.send_text("ping")
            assert ws.receive_text() == "ping"


def test_token_preferred_when_both_credentials_given(
    client: TestClient, mustang_home: Path
) -> None:
    """Ambiguous credentials → token wins (see transport.md)."""
    with client:
        token = _read_token(mustang_home)
        with client.websocket_connect(f"/session?token={token}&password=whatever") as ws:
            ws.send_text("both")
            assert ws.receive_text() == "both"


# --------------------------------------------------------------------
# Unknown stack name — should abort kernel boot at flag-register time
# --------------------------------------------------------------------


def test_unknown_stack_name_aborts_boot(
    mustang_home: Path,
) -> None:
    """Writing an unknown ``transport.stack`` name fails kernel startup.

    Because :class:`TransportFlags` types the field as a
    ``Literal["dummy"]``, pydantic rejects anything else during
    ``FlagManager.register``, which the lifespan converts into a
    fatal error.
    """
    flags_path = mustang_home / ".mustang" / "flags.yaml"
    flags_path.parent.mkdir(parents=True, exist_ok=True)
    flags_path.write_text(yaml.safe_dump({"transport": {"stack": "definitely-not-a-stack"}}))

    app = app_module.create_app()
    client = TestClient(app)
    with pytest.raises(Exception):
        # TestClient triggers the lifespan on context entry; the
        # lifespan's flags.register call raises pydantic
        # ValidationError which we expect to propagate.
        with client:
            pass
