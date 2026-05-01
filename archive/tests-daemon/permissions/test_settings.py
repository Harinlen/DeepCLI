"""Tests for PermissionSettings — persistence of allow/deny rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daemon.permissions.settings import PermissionSettings


def _write_settings(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class TestLoad:
    """Load paths."""

    def test_missing_file(self, tmp_path: Path) -> None:
        """Missing file → empty rule lists, no crash."""
        settings = PermissionSettings(tmp_path / "settings.json")
        settings.load()
        assert settings.allow_rules == []
        assert settings.deny_rules == []

    def test_parses_allow_and_deny(self, tmp_path: Path) -> None:
        """Load allow + deny rules from JSON."""
        path = tmp_path / "settings.json"
        _write_settings(
            path,
            {
                "permissions": {
                    "allow": ["Bash(git *)", "file_read"],
                    "deny": ["Bash(rm -rf *)"],
                }
            },
        )
        settings = PermissionSettings(path)
        settings.load()
        assert [r.rule_str for r in settings.allow_rules] == [
            "Bash(git *)",
            "file_read",
        ]
        assert [r.rule_str for r in settings.deny_rules] == ["Bash(rm -rf *)"]

    def test_skips_malformed_rules(self, tmp_path: Path) -> None:
        """Malformed or non-string rules are dropped, not fatal."""
        path = tmp_path / "settings.json"
        _write_settings(
            path,
            {
                "permissions": {
                    "allow": ["Bash(valid)", 123, "(((broken", "grep"],
                }
            },
        )
        settings = PermissionSettings(path)
        settings.load()
        rule_strs = [r.rule_str for r in settings.allow_rules]
        assert "Bash(valid)" in rule_strs
        assert "grep" in rule_strs
        assert 123 not in rule_strs

    def test_malformed_json_does_not_raise(self, tmp_path: Path) -> None:
        """Corrupt file is logged and ignored."""
        path = tmp_path / "settings.json"
        path.write_text("not json {{", encoding="utf-8")
        settings = PermissionSettings(path)
        settings.load()
        assert settings.allow_rules == []

    def test_non_object_json(self, tmp_path: Path) -> None:
        """Top-level array (not object) is ignored."""
        path = tmp_path / "settings.json"
        path.write_text("[1,2,3]", encoding="utf-8")
        settings = PermissionSettings(path)
        settings.load()
        assert settings.allow_rules == []


class TestSave:
    """Save paths."""

    def test_save_creates_file(self, tmp_path: Path) -> None:
        """save() creates parent dir + file."""
        path = tmp_path / "sub" / "settings.json"
        settings = PermissionSettings(path)
        settings.add_allow_rule("grep")
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["permissions"]["allow"] == ["grep"]

    def test_roundtrip_preserves_order(self, tmp_path: Path) -> None:
        """Save + load preserves rule order."""
        path = tmp_path / "settings.json"
        settings = PermissionSettings(path)
        settings.add_allow_rule("Bash(git *)")
        settings.add_allow_rule("grep")
        settings.add_deny_rule("Bash(rm *)")

        # New instance reads what the first wrote.
        reloaded = PermissionSettings(path)
        reloaded.load()
        assert [r.rule_str for r in reloaded.allow_rules] == ["Bash(git *)", "grep"]
        assert [r.rule_str for r in reloaded.deny_rules] == ["Bash(rm *)"]

    def test_save_preserves_other_fields(self, tmp_path: Path) -> None:
        """Non-permissions top-level keys round-trip intact."""
        path = tmp_path / "settings.json"
        _write_settings(
            path,
            {
                "theme": "dark",
                "permissions": {"allow": ["old"]},
                "editor": {"font": "JetBrains Mono"},
            },
        )
        settings = PermissionSettings(path)
        settings.load()
        settings.add_allow_rule("grep")

        final = json.loads(path.read_text())
        assert final["theme"] == "dark"
        assert final["editor"] == {"font": "JetBrains Mono"}
        assert final["permissions"]["allow"] == ["old", "grep"]


class TestMutation:
    """Add / remove / dedupe."""

    def test_add_dedupes(self, tmp_path: Path) -> None:
        """Adding a duplicate rule_str is a no-op."""
        settings = PermissionSettings(tmp_path / "settings.json")
        assert settings.add_allow_rule("Bash(git *)") is True
        assert settings.add_allow_rule("Bash(git *)") is False
        assert len(settings.allow_rules) == 1

    def test_add_malformed_raises(self, tmp_path: Path) -> None:
        """Malformed rule string raises ValueError."""
        settings = PermissionSettings(tmp_path / "settings.json")
        with pytest.raises(ValueError):
            settings.add_allow_rule("((")

    def test_remove_rule_from_allow(self, tmp_path: Path) -> None:
        """remove_rule removes from allow list and persists."""
        settings = PermissionSettings(tmp_path / "settings.json")
        settings.add_allow_rule("grep")
        assert settings.remove_rule("grep") is True
        assert settings.allow_rules == []

    def test_remove_rule_from_deny(self, tmp_path: Path) -> None:
        """remove_rule also checks deny list."""
        settings = PermissionSettings(tmp_path / "settings.json")
        settings.add_deny_rule("Bash(rm *)")
        assert settings.remove_rule("Bash(rm *)") is True
        assert settings.deny_rules == []

    def test_remove_rule_not_found(self, tmp_path: Path) -> None:
        """remove_rule returns False if the rule is absent."""
        settings = PermissionSettings(tmp_path / "settings.json")
        assert settings.remove_rule("nope") is False
