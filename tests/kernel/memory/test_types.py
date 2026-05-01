"""Tests for memory.types — data classes and hotness classification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kernel.memory.types import (
    EVERGREEN_CATEGORIES,
    SOURCE_WEIGHTS,
    DispositionConfig,
    MemoryEntry,
    MemoryHeader,
    ScoredMemory,
    classify_hotness,
)


def _make_header(
    *,
    category: str = "semantic",
    source: str = "agent",
    access_count: int = 0,
    age_days: int = 0,
    locked: bool = False,
) -> MemoryHeader:
    now = datetime.now(timezone.utc)
    return MemoryHeader(
        filename="test",
        name="test",
        description="test description",
        category=category,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        created=now - timedelta(days=age_days),
        updated=now - timedelta(days=age_days),
        access_count=access_count,
        locked=locked,
        rel_path=f"{category}/test.md",
    )


class TestMemoryHeader:
    def test_age_days_zero(self) -> None:
        h = _make_header(age_days=0)
        assert h.age_days == 0

    def test_age_days_positive(self) -> None:
        h = _make_header(age_days=10)
        assert h.age_days == 10

    def test_evergreen_profile(self) -> None:
        h = _make_header(category="profile")
        assert h.evergreen is True

    def test_evergreen_semantic(self) -> None:
        h = _make_header(category="semantic")
        assert h.evergreen is True

    def test_evergreen_procedural(self) -> None:
        h = _make_header(category="procedural")
        assert h.evergreen is True

    def test_not_evergreen_episodic(self) -> None:
        h = _make_header(category="episodic")
        assert h.evergreen is False

    def test_evergreen_categories_set(self) -> None:
        assert "profile" in EVERGREEN_CATEGORIES
        assert "episodic" not in EVERGREEN_CATEGORIES


class TestMemoryEntry:
    def test_proxies(self) -> None:
        h = _make_header(category="profile", source="user", access_count=5)
        e = MemoryEntry(header=h, content="body text")
        assert e.name == "test"
        assert e.category == "profile"
        assert e.source == "user"
        assert e.access_count == 5
        assert e.content == "body text"


class TestScoredMemory:
    def test_fields(self) -> None:
        h = _make_header()
        sm = ScoredMemory(header=h, relevance=4, reason="relevant", final_score=2.5)
        assert sm.relevance == 4
        assert sm.final_score == 2.5


class TestHotness:
    def test_hot(self) -> None:
        assert classify_hotness(0.7) == "hot"

    def test_warm(self) -> None:
        assert classify_hotness(0.4) == "warm"

    def test_cold(self) -> None:
        assert classify_hotness(0.1) == "cold"

    def test_boundary_hot(self) -> None:
        assert classify_hotness(0.61) == "hot"

    def test_boundary_cold(self) -> None:
        assert classify_hotness(0.19) == "cold"

    def test_boundary_warm_lower(self) -> None:
        assert classify_hotness(0.2) == "warm"

    def test_boundary_warm_upper(self) -> None:
        assert classify_hotness(0.6) == "warm"


class TestSourceWeights:
    def test_user_highest(self) -> None:
        assert SOURCE_WEIGHTS["user"] > SOURCE_WEIGHTS["agent"]
        assert SOURCE_WEIGHTS["agent"] > SOURCE_WEIGHTS["extracted"]


class TestDispositionConfig:
    def test_defaults(self) -> None:
        d = DispositionConfig()
        assert d.skepticism == 3
        assert d.recency_bias == 3
        assert d.verbosity == 3
