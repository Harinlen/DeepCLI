"""Tests for kernel.prompts.PromptManager."""

from __future__ import annotations

from pathlib import Path

import pytest

from kernel.prompts import PromptKeyError, PromptLoadError, PromptManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_prompts(tmp_path: Path) -> Path:
    """Create a minimal prompt directory tree for testing."""
    mod_dir = tmp_path / "orchestrator"
    mod_dir.mkdir()
    (mod_dir / "base.txt").write_text("You are a helpful assistant.", encoding="utf-8")
    (mod_dir / "greeting.txt").write_text("Hello, {name}!", encoding="utf-8")

    auth_dir = tmp_path / "tool_authz"
    auth_dir.mkdir()
    (auth_dir / "system.txt").write_text("Classify this command.", encoding="utf-8")

    return tmp_path


@pytest.fixture()
def loaded_manager(tmp_prompts: Path) -> PromptManager:
    """Return a PromptManager that has already loaded the tmp_prompts tree."""
    pm = PromptManager(defaults_dir=tmp_prompts)
    pm.load()
    return pm


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------


class TestLoad:
    def test_loads_all_txt_files(self, loaded_manager: PromptManager) -> None:
        keys = loaded_manager.keys()
        assert "orchestrator/base" in keys
        assert "orchestrator/greeting" in keys
        assert "tool_authz/system" in keys
        assert len(keys) == 3

    def test_missing_directory_raises(self, tmp_path: Path) -> None:
        pm = PromptManager(defaults_dir=tmp_path / "nonexistent")
        with pytest.raises(PromptLoadError, match="not found"):
            pm.load()

    def test_empty_directory_loads_zero(self, tmp_path: Path) -> None:
        pm = PromptManager(defaults_dir=tmp_path)
        pm.load()
        assert pm.keys() == []

    def test_ignores_non_txt_files(self, tmp_prompts: Path) -> None:
        (tmp_prompts / "orchestrator" / "notes.md").write_text("ignore me")
        (tmp_prompts / "data.yaml").write_text("ignore: true")
        pm = PromptManager(defaults_dir=tmp_prompts)
        pm.load()
        assert len(pm.keys()) == 3  # only .txt files


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------


class TestGet:
    def test_returns_exact_text(self, loaded_manager: PromptManager) -> None:
        assert loaded_manager.get("orchestrator/base") == "You are a helpful assistant."

    def test_missing_key_raises(self, loaded_manager: PromptManager) -> None:
        with pytest.raises(PromptKeyError):
            loaded_manager.get("nonexistent/key")


# ---------------------------------------------------------------------------
# render()
# ---------------------------------------------------------------------------


class TestRender:
    def test_renders_template(self, loaded_manager: PromptManager) -> None:
        result = loaded_manager.render("orchestrator/greeting", name="World")
        assert result == "Hello, World!"

    def test_missing_placeholder_raises(self, loaded_manager: PromptManager) -> None:
        with pytest.raises(KeyError):
            loaded_manager.render("orchestrator/greeting")  # {name} not provided

    def test_missing_key_raises(self, loaded_manager: PromptManager) -> None:
        with pytest.raises(PromptKeyError):
            loaded_manager.render("no/such/key", x="y")


# ---------------------------------------------------------------------------
# has()
# ---------------------------------------------------------------------------


class TestHas:
    def test_returns_true_for_loaded_key(self, loaded_manager: PromptManager) -> None:
        assert loaded_manager.has("orchestrator/base") is True

    def test_returns_false_for_missing_key(self, loaded_manager: PromptManager) -> None:
        assert loaded_manager.has("missing") is False


# ---------------------------------------------------------------------------
# User override layers
# ---------------------------------------------------------------------------


class TestUserOverrides:
    def test_user_dir_overrides_default(self, tmp_path: Path) -> None:
        defaults = tmp_path / "defaults"
        defaults.mkdir()
        (defaults / "orchestrator").mkdir()
        (defaults / "orchestrator" / "base.txt").write_text("default text")

        user = tmp_path / "user"
        user.mkdir()
        (user / "orchestrator").mkdir()
        (user / "orchestrator" / "base.txt").write_text("user override")

        pm = PromptManager(defaults_dir=defaults, user_dirs=[user])
        pm.load()
        assert pm.get("orchestrator/base") == "user override"

    def test_user_dir_adds_new_keys(self, tmp_path: Path) -> None:
        defaults = tmp_path / "defaults"
        defaults.mkdir()

        user = tmp_path / "user"
        user.mkdir()
        (user / "custom.txt").write_text("custom prompt")

        pm = PromptManager(defaults_dir=defaults, user_dirs=[user])
        pm.load()
        assert pm.get("custom") == "custom prompt"

    def test_project_dir_overrides_global(self, tmp_path: Path) -> None:
        defaults = tmp_path / "defaults"
        defaults.mkdir()
        (defaults / "base.txt").write_text("default")

        global_user = tmp_path / "global"
        global_user.mkdir()
        (global_user / "base.txt").write_text("global override")

        project_user = tmp_path / "project"
        project_user.mkdir()
        (project_user / "base.txt").write_text("project override")

        pm = PromptManager(defaults_dir=defaults, user_dirs=[global_user, project_user])
        pm.load()
        assert pm.get("base") == "project override"

    def test_missing_user_dir_silently_skipped(self, tmp_path: Path) -> None:
        defaults = tmp_path / "defaults"
        defaults.mkdir()
        (defaults / "base.txt").write_text("default text")

        missing = tmp_path / "nonexistent_user_dir"
        pm = PromptManager(defaults_dir=defaults, user_dirs=[missing])
        pm.load()  # must not raise
        assert pm.get("base") == "default text"

    def test_no_user_dirs_behaves_as_before(self, tmp_prompts: Path) -> None:
        pm = PromptManager(defaults_dir=tmp_prompts)
        pm.load()
        assert pm.get("orchestrator/base") == "You are a helpful assistant."

    def test_keys_union_of_all_layers(self, tmp_path: Path) -> None:
        defaults = tmp_path / "defaults"
        defaults.mkdir()
        (defaults / "a.txt").write_text("a")

        user = tmp_path / "user"
        user.mkdir()
        (user / "b.txt").write_text("b")

        pm = PromptManager(defaults_dir=defaults, user_dirs=[user])
        pm.load()
        assert "a" in pm.keys()
        assert "b" in pm.keys()


# ---------------------------------------------------------------------------
# Default directory (shipped prompts)
# ---------------------------------------------------------------------------


class TestDefaultDirectory:
    """Verify that the shipped default/ directory loads without errors."""

    def test_default_prompts_load(self) -> None:
        pm = PromptManager()
        pm.load()
        # Must load at least the known prompt files.
        assert pm.has("orchestrator/base")
        assert pm.has("orchestrator/compact_system")
        assert pm.has("orchestrator/compact_prefix")
        assert pm.has("orchestrator/compact_fallback")
        assert pm.has("orchestrator/summary_header")
        assert pm.has("orchestrator/system_reminder")
        assert pm.has("tool_authz/bash_classifier_system")
        assert pm.has("tool_authz/bash_classifier_user")

    def test_summary_header_template_renders(self) -> None:
        pm = PromptManager()
        pm.load()
        result = pm.render("orchestrator/summary_header", summary="test summary")
        assert "test summary" in result
        assert "Prior conversation summary" in result

    def test_system_reminder_template_renders(self) -> None:
        pm = PromptManager()
        pm.load()
        result = pm.render("orchestrator/system_reminder", reminder="hello")
        assert "<system-reminder>" in result
        assert "hello" in result

    def test_bash_classifier_user_template_renders(self) -> None:
        pm = PromptManager()
        pm.load()
        result = pm.render(
            "tool_authz/bash_classifier_user",
            command="ls -la",
            cwd="/home/user",
        )
        assert "ls -la" in result
        assert "/home/user" in result
