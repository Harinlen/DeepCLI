"""Tests for config loader — YAML parsing, env var substitution."""

from pathlib import Path

import pytest

from daemon.config.loader import load_config
from daemon.errors import ConfigError


class TestLoadConfig:
    def test_load_missing_file_returns_defaults(self, tmp_path: Path):
        """Non-existent config file → pure defaults."""
        result = load_config(tmp_path / "nonexistent.yaml")
        assert result.default_provider == "local"
        assert result.daemon.port == 7777

    def test_load_empty_file_returns_defaults(self, tmp_path: Path):
        """Empty YAML file → pure defaults."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("")
        result = load_config(config_path)
        assert result.default_provider == "local"

    def test_load_valid_yaml(self, tmp_path: Path):
        """Valid YAML is parsed correctly."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """\
provider:
  default: custom
  custom:
    base_url: http://localhost:5000/v1
    model: test-model
    api_key: test-key
daemon:
  port: 9999
"""
        )
        result = load_config(config_path)
        assert result.default_provider == "custom"
        assert result.providers["custom"].model == "test-model"
        assert result.daemon.port == 9999

    def test_env_var_substitution(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """${VAR} references are replaced with env var values."""
        monkeypatch.setenv("TEST_API_KEY", "secret-key-123")
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """\
provider:
  default: test
  test:
    base_url: http://localhost:8080/v1
    model: my-model
    api_key: ${TEST_API_KEY}
"""
        )
        result = load_config(config_path)
        assert result.providers["test"].api_key == "secret-key-123"

    def test_missing_env_var_becomes_empty_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Missing env var → empty string (not crash)."""
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """\
provider:
  default: test
  test:
    base_url: http://localhost:8080/v1
    model: m
    api_key: ${NONEXISTENT_VAR}
"""
        )
        result = load_config(config_path)
        # Missing env var → empty string (C4 fix: `is not None` preserves empty)
        assert result.providers["test"].api_key == ""

    def test_invalid_yaml_raises_config_error(self, tmp_path: Path):
        """Malformed YAML → ConfigError."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("{{invalid yaml")
        with pytest.raises(ConfigError):
            load_config(config_path)
