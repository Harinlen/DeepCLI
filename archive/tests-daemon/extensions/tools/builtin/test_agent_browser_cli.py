"""Tests for agent_browser_cli — Chrome pinning + env construction."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from daemon.extensions.tools.builtin import agent_browser_cli


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any inherited AGENT_BROWSER_EXECUTABLE_PATH so tests are isolated."""
    monkeypatch.delenv("AGENT_BROWSER_EXECUTABLE_PATH", raising=False)


class TestFindInstalledChrome:
    def test_returns_none_when_cache_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        nonexistent = tmp_path / "no-such-cache"
        monkeypatch.setattr(agent_browser_cli, "_BROWSERS_CACHE", nonexistent)
        assert agent_browser_cli.find_installed_chrome() is None

    def test_returns_none_when_cache_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty_cache = tmp_path / "empty"
        empty_cache.mkdir()
        monkeypatch.setattr(agent_browser_cli, "_BROWSERS_CACHE", empty_cache)
        assert agent_browser_cli.find_installed_chrome() is None

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX path layout")
    def test_finds_chrome_in_linux_layout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache = tmp_path / "browsers"
        version_dir = cache / "chrome-1234"
        nested = version_dir / "chrome-linux64"
        nested.mkdir(parents=True)
        chrome = nested / "chrome"
        chrome.write_text("#!/bin/sh\necho fake-chrome\n")
        chrome.chmod(0o755)

        monkeypatch.setattr(agent_browser_cli, "_BROWSERS_CACHE", cache)
        if sys.platform == "darwin":
            # macOS layout differs; skip on darwin even though we ran here.
            pytest.skip("linux-specific layout")

        found = agent_browser_cli.find_installed_chrome()
        # On Linux this should match.  On other POSIX-like platforms
        # we don't fail the test if the layout doesn't match.
        if sys.platform.startswith("linux"):
            assert found == chrome


class TestEnv:
    def test_pins_chrome_when_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_chrome = tmp_path / "chrome"
        fake_chrome.write_text("")
        monkeypatch.setattr(
            agent_browser_cli,
            "find_installed_chrome",
            lambda: fake_chrome,
        )
        env = agent_browser_cli.env()
        assert env["AGENT_BROWSER_EXECUTABLE_PATH"] == str(fake_chrome)

    def test_drops_inherited_executable_path_when_chrome_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_BROWSER_EXECUTABLE_PATH", "/usr/bin/chromium")
        monkeypatch.setattr(
            agent_browser_cli,
            "find_installed_chrome",
            lambda: None,
        )
        env = agent_browser_cli.env()
        # Must not silently fall back to whatever the user had set.
        assert "AGENT_BROWSER_EXECUTABLE_PATH" not in env

    def test_overwrites_inherited_executable_path_when_chrome_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_chrome = tmp_path / "chrome"
        fake_chrome.write_text("")
        monkeypatch.setenv("AGENT_BROWSER_EXECUTABLE_PATH", "/usr/bin/chromium")
        monkeypatch.setattr(
            agent_browser_cli,
            "find_installed_chrome",
            lambda: fake_chrome,
        )
        env = agent_browser_cli.env()
        # The user's value must be overwritten by our pinned path.
        assert env["AGENT_BROWSER_EXECUTABLE_PATH"] == str(fake_chrome)
        assert env["AGENT_BROWSER_EXECUTABLE_PATH"] != "/usr/bin/chromium"

    def test_sets_idle_timeout_and_max_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            agent_browser_cli,
            "find_installed_chrome",
            lambda: None,
        )
        env = agent_browser_cli.env()
        assert env["AGENT_BROWSER_IDLE_TIMEOUT_MS"] == str(5 * 60 * 1000)
        assert int(env["AGENT_BROWSER_MAX_OUTPUT"]) > 0

    def test_passes_no_sandbox_to_chrome(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Required on Ubuntu 23.10+, containers, and VMs."""
        monkeypatch.setattr(
            agent_browser_cli,
            "find_installed_chrome",
            lambda: None,
        )
        env = agent_browser_cli.env()
        assert "--no-sandbox" in env.get("AGENT_BROWSER_ARGS", "")

    def test_disables_automation_controlled_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without this flag, sites that block headless browsers (BOM,
        Cloudflare-fronted pages) refuse to serve content."""
        monkeypatch.setattr(
            agent_browser_cli,
            "find_installed_chrome",
            lambda: None,
        )
        env = agent_browser_cli.env()
        assert "AutomationControlled" in env.get("AGENT_BROWSER_ARGS", "")

    def test_sets_real_user_agent_when_chrome_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Falls back to a generic recent Chrome UA when version
        can't be detected. Default HeadlessChrome trips bot detection."""
        monkeypatch.setattr(
            agent_browser_cli,
            "find_installed_chrome",
            lambda: None,
        )
        env = agent_browser_cli.env()
        ua = env.get("AGENT_BROWSER_USER_AGENT", "")
        assert ua.startswith("Mozilla/5.0")
        assert "HeadlessChrome" not in ua
        assert "Chrome/" in ua

    def test_user_agent_uses_bundled_chrome_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The UA's Chrome version should match the bundled Chrome's
        major version, so it auto-tracks installs without us pinning
        a specific version."""
        # Build a fake Chrome at the path agent-browser would use:
        # ~/.agent-browser/browsers/chrome-<version>/chrome
        version_dir = tmp_path / "chrome-152.0.9999.0"
        version_dir.mkdir()
        chrome = version_dir / "chrome"
        chrome.write_text("")
        monkeypatch.setattr(
            agent_browser_cli,
            "find_installed_chrome",
            lambda: chrome,
        )
        env = agent_browser_cli.env()
        ua = env.get("AGENT_BROWSER_USER_AGENT", "")
        assert "Chrome/152.0.0.0" in ua
        assert "HeadlessChrome" not in ua


class TestChromeVersionParsing:
    def test_extracts_major_version_from_path(self, tmp_path: Path) -> None:
        chrome = tmp_path / "chrome-147.0.7727.56" / "chrome"
        chrome.parent.mkdir()
        chrome.write_text("")
        assert agent_browser_cli._chrome_major_version(chrome) == 147

    def test_extracts_version_from_nested_path(self, tmp_path: Path) -> None:
        # Linux layout: chrome-<ver>/chrome-linux64/chrome
        nested = tmp_path / "chrome-150.0.0.0" / "chrome-linux64"
        nested.mkdir(parents=True)
        chrome = nested / "chrome"
        chrome.write_text("")
        assert agent_browser_cli._chrome_major_version(chrome) == 150

    def test_returns_none_for_unmatched_path(self, tmp_path: Path) -> None:
        chrome = tmp_path / "weird-name" / "chrome"
        chrome.parent.mkdir()
        chrome.write_text("")
        assert agent_browser_cli._chrome_major_version(chrome) is None


class TestIsAvailable:
    def test_requires_both_cli_and_chrome(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_cli = tmp_path / "agent-browser"
        fake_cli.write_text("")
        fake_chrome = tmp_path / "chrome"
        fake_chrome.write_text("")
        monkeypatch.setattr(agent_browser_cli, "AGENT_BROWSER_CLI", fake_cli)
        monkeypatch.setattr(
            agent_browser_cli,
            "find_installed_chrome",
            lambda: fake_chrome,
        )
        assert agent_browser_cli.is_available() is True

    def test_returns_false_when_cli_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_chrome = tmp_path / "chrome"
        fake_chrome.write_text("")
        monkeypatch.setattr(
            agent_browser_cli,
            "AGENT_BROWSER_CLI",
            tmp_path / "no-such-binary",
        )
        monkeypatch.setattr(
            agent_browser_cli,
            "find_installed_chrome",
            lambda: fake_chrome,
        )
        assert agent_browser_cli.is_available() is False

    def test_returns_false_when_chrome_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_cli = tmp_path / "agent-browser"
        fake_cli.write_text("")
        monkeypatch.setattr(agent_browser_cli, "AGENT_BROWSER_CLI", fake_cli)
        monkeypatch.setattr(
            agent_browser_cli,
            "find_installed_chrome",
            lambda: None,
        )
        assert agent_browser_cli.is_available() is False


class TestInstallHint:
    def test_mentions_npm_install(self) -> None:
        hint = agent_browser_cli.install_hint()
        assert "npm install" in hint
        assert "agent-browser install" in hint
        assert "Chrome" in hint
