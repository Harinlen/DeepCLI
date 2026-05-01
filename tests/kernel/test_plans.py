"""Unit tests for kernel.plans — plan file management."""

from __future__ import annotations

from pathlib import Path

import pytest

from kernel.plans import (
    clear_slug_cache,
    get_plan,
    get_plan_file_path,
    get_plan_slug,
    get_plans_directory,
    is_session_plan_file,
)


@pytest.fixture(autouse=True)
def _clean_slug_cache():
    """Clear the module-level slug cache before each test."""
    clear_slug_cache()
    yield
    clear_slug_cache()


@pytest.fixture()
def plans_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Override plans directory to a temp dir."""
    d = tmp_path / "plans"
    d.mkdir()
    monkeypatch.setenv("MUSTANG_PLANS_DIR", str(d))
    return d


class TestGetPlansDirectory:
    def test_returns_path(self, plans_dir: Path) -> None:
        result = get_plans_directory()
        assert result == plans_dir

    def test_creates_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        d = tmp_path / "new_plans"
        monkeypatch.setenv("MUSTANG_PLANS_DIR", str(d))
        assert not d.exists()
        result = get_plans_directory()
        assert result == d
        assert d.exists()


class TestGetPlanSlug:
    def test_generates_slug(self, plans_dir: Path) -> None:
        slug = get_plan_slug("sess-1")
        assert isinstance(slug, str)
        assert "-" in slug  # adjective-noun format

    def test_cached_per_session(self, plans_dir: Path) -> None:
        slug1 = get_plan_slug("sess-1")
        slug2 = get_plan_slug("sess-1")
        assert slug1 == slug2

    def test_different_sessions(self, plans_dir: Path) -> None:
        # Different sessions *may* get different slugs (not guaranteed
        # due to random, but extremely unlikely to collide with 2500 combos).
        slugs = {get_plan_slug(f"sess-{i}") for i in range(10)}
        assert len(slugs) > 1

    def test_avoids_collision(self, plans_dir: Path) -> None:
        # Pre-create a file to force retry.
        first_slug = get_plan_slug("sess-probe")
        clear_slug_cache("sess-probe")
        (plans_dir / f"{first_slug}.md").write_text("occupied")
        # Next call should avoid the collision (probabilistic but near-certain).
        # We just verify it doesn't crash.
        slug = get_plan_slug("sess-probe")
        assert isinstance(slug, str)


class TestGetPlanFilePath:
    def test_main_session(self, plans_dir: Path) -> None:
        path = get_plan_file_path("s1")
        assert path.parent == plans_dir
        assert path.suffix == ".md"
        assert "agent" not in path.name

    def test_subagent(self, plans_dir: Path) -> None:
        path = get_plan_file_path("s1", agent_id="agent-42")
        assert "-agent-agent-42" in path.name
        assert path.suffix == ".md"


class TestGetPlan:
    def test_returns_none_when_missing(self, plans_dir: Path) -> None:
        assert get_plan("s1") is None

    def test_reads_existing(self, plans_dir: Path) -> None:
        path = get_plan_file_path("s1")
        path.write_text("# My Plan\nDo stuff.", encoding="utf-8")
        assert get_plan("s1") == "# My Plan\nDo stuff."


class TestIsSessionPlanFile:
    def test_matches_main(self, plans_dir: Path) -> None:
        path = get_plan_file_path("s1")
        assert is_session_plan_file(path, "s1") is True

    def test_matches_agent(self, plans_dir: Path) -> None:
        path = get_plan_file_path("s1", agent_id="a1")
        assert is_session_plan_file(path, "s1") is True

    def test_rejects_different_session(self, plans_dir: Path) -> None:
        path = get_plan_file_path("s1")
        assert is_session_plan_file(path, "s2") is False

    def test_rejects_non_md(self, plans_dir: Path) -> None:
        slug = get_plan_slug("s1")
        fake = plans_dir / f"{slug}.txt"
        assert is_session_plan_file(fake, "s1") is False

    def test_rejects_traversal(self, plans_dir: Path) -> None:
        slug = get_plan_slug("s1")
        traversal = plans_dir / f"{slug}/../../../etc/passwd.md"
        assert is_session_plan_file(traversal, "s1") is False
