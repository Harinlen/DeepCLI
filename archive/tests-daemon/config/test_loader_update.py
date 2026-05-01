"""Tests for config loader — update_provider_field."""

from __future__ import annotations

from pathlib import Path

import yaml

from daemon.config.loader import update_provider_field


class TestUpdateProviderField:
    """Tests for update_provider_field."""

    def test_writes_field(self, tmp_path: Path) -> None:
        """Writes a new field to an existing provider section."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "provider": {
                        "minimax": {
                            "type": "minimax",
                            "base_url": "https://api.minimax.io/v1",
                            "model": "MiniMax-M2.7",
                            "api_key": "test",
                        }
                    }
                }
            )
        )

        result = update_provider_field("minimax", "context_window", 204800, path=config_path)
        assert result is True

        raw = yaml.safe_load(config_path.read_text())
        assert raw["provider"]["minimax"]["context_window"] == 204800
        # Other fields preserved
        assert raw["provider"]["minimax"]["model"] == "MiniMax-M2.7"

    def test_skips_if_same_value(self, tmp_path: Path) -> None:
        """Skips write if field already has the same value."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "provider": {
                        "minimax": {
                            "type": "minimax",
                            "context_window": 204800,
                        }
                    }
                }
            )
        )

        result = update_provider_field("minimax", "context_window", 204800, path=config_path)
        assert result is False

    def test_skips_missing_provider(self, tmp_path: Path) -> None:
        """Skips if the provider section doesn't exist."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "provider": {
                        "local": {"type": "openai_compatible"},
                    }
                }
            )
        )

        result = update_provider_field("minimax", "context_window", 204800, path=config_path)
        assert result is False

    def test_skips_string_provider_ref(self, tmp_path: Path) -> None:
        """Skips if provider value is a bare string (e.g. 'default: minimax')."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "provider": {
                        "default": "minimax",
                    }
                }
            )
        )

        result = update_provider_field("default", "context_window", 204800, path=config_path)
        assert result is False

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Creates parent directories if needed."""
        config_path = tmp_path / "subdir" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            yaml.dump(
                {
                    "provider": {
                        "minimax": {"type": "minimax"},
                    }
                }
            )
        )

        result = update_provider_field("minimax", "context_window", 204800, path=config_path)
        assert result is True

    def test_handles_missing_file(self, tmp_path: Path) -> None:
        """Returns False when config file doesn't exist."""
        config_path = tmp_path / "nonexistent.yaml"
        result = update_provider_field("minimax", "context_window", 204800, path=config_path)
        assert result is False

    def test_overwrites_different_value(self, tmp_path: Path) -> None:
        """Overwrites an existing field with a different value."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "provider": {
                        "minimax": {
                            "type": "minimax",
                            "context_window": 32000,
                        }
                    }
                }
            )
        )

        result = update_provider_field("minimax", "context_window", 204800, path=config_path)
        assert result is True

        raw = yaml.safe_load(config_path.read_text())
        assert raw["provider"]["minimax"]["context_window"] == 204800
