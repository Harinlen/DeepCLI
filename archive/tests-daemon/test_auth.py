"""Tests for file-based local authentication."""

from pathlib import Path
from unittest.mock import patch

from daemon.auth import (
    cleanup_auth_token,
    ensure_auth_token,
    load_auth_token,
    verify_token,
)


class TestAuthToken:
    """Tests for token generation, loading, and verification."""

    def test_generate_and_load(self, tmp_path: Path) -> None:
        token_path = tmp_path / ".auth_token"
        with (
            patch("daemon.auth.AUTH_DIR", tmp_path),
            patch("daemon.auth.AUTH_TOKEN_PATH", token_path),
        ):
            token = ensure_auth_token()
            assert len(token) > 0
            loaded = load_auth_token()
            assert loaded == token

    def test_token_file_permissions(self, tmp_path: Path) -> None:
        token_path = tmp_path / ".auth_token"
        with (
            patch("daemon.auth.AUTH_DIR", tmp_path),
            patch("daemon.auth.AUTH_TOKEN_PATH", token_path),
        ):
            ensure_auth_token()
            mode = token_path.stat().st_mode & 0o777
            assert mode == 0o600

    def test_ensure_reuses_existing(self, tmp_path: Path) -> None:
        """ensure_auth_token returns the same token on subsequent calls."""
        token_path = tmp_path / ".auth_token"
        with (
            patch("daemon.auth.AUTH_DIR", tmp_path),
            patch("daemon.auth.AUTH_TOKEN_PATH", token_path),
        ):
            first = ensure_auth_token()
            second = ensure_auth_token()
            assert first == second

    def test_load_missing_token(self, tmp_path: Path) -> None:
        token_path = tmp_path / ".auth_token"
        with patch("daemon.auth.AUTH_TOKEN_PATH", token_path):
            assert load_auth_token() is None

    def test_verify_token_correct(self) -> None:
        assert verify_token("abc123", "abc123") is True

    def test_verify_token_wrong(self) -> None:
        assert verify_token("abc123", "wrong") is False

    def test_verify_token_empty(self) -> None:
        assert verify_token("", "abc123") is False

    def test_cleanup(self, tmp_path: Path) -> None:
        token_path = tmp_path / ".auth_token"
        token_path.write_text("token")
        with patch("daemon.auth.AUTH_TOKEN_PATH", token_path):
            cleanup_auth_token()
            assert not token_path.exists()

    def test_cleanup_missing_file(self, tmp_path: Path) -> None:
        token_path = tmp_path / ".auth_token"
        with patch("daemon.auth.AUTH_TOKEN_PATH", token_path):
            # Should not raise
            cleanup_auth_token()
