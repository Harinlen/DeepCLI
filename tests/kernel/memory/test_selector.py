"""Tests for memory.selector — BM25, manifest builder, ranking."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from kernel.memory.selector import BM25Index, RelevanceSelector, build_manifest
from kernel.memory.types import MemoryHeader, ScoredMemory


def _make_header(
    filename: str = "test",
    category: str = "semantic",
    description: str = "test description",
    source: str = "agent",
    access_count: int = 0,
    age_days: int = 0,
) -> MemoryHeader:
    now = datetime.now(timezone.utc)
    return MemoryHeader(
        filename=filename,
        name=filename,
        description=description,
        category=category,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        created=now - timedelta(days=age_days),
        updated=now - timedelta(days=age_days),
        access_count=access_count,
        locked=False,
        rel_path=f"{category}/{filename}.md",
    )


class TestBM25Index:
    def test_build_and_query(self) -> None:
        bm25 = BM25Index()
        headers = [
            _make_header("python", description="Python programming language and FastAPI"),
            _make_header("react", description="React frontend framework with TypeScript"),
            _make_header("deploy", description="Kubernetes deployment and Docker"),
        ]
        bm25.build(headers)
        results = bm25.query("Python FastAPI backend")
        assert len(results) > 0
        # Python memory should rank first
        assert results[0][0].filename == "python"

    def test_empty_index(self) -> None:
        bm25 = BM25Index()
        bm25.build([])
        results = bm25.query("anything")
        assert results == []

    def test_empty_query(self) -> None:
        bm25 = BM25Index()
        bm25.build([_make_header(description="something")])
        results = bm25.query("")
        assert results == []

    def test_chinese_tokenization(self) -> None:
        """BM25 should work with Chinese text (jieba)."""
        bm25 = BM25Index()
        headers = [
            _make_header("cn-user", description="用户是一名后端工程师，使用 Python 开发"),
            _make_header("cn-proj", description="项目使用 FastAPI 框架和 PostgreSQL 数据库"),
        ]
        bm25.build(headers)
        results = bm25.query("后端工程师 Python")
        assert len(results) > 0
        assert results[0][0].filename == "cn-user"

    def test_sigmoid_normalized_scores(self) -> None:
        """BM25 scores should be normalized to 0-1 range."""
        bm25 = BM25Index()
        headers = [_make_header(description="Python programming language")]
        bm25.build(headers)
        results = bm25.query("Python")
        assert len(results) == 1
        _, score = results[0]
        assert 0 < score <= 1.0


class TestBuildManifest:
    def test_alias_mapping(self) -> None:
        headers = [
            _make_header("mem-a", "profile", "profile info"),
            _make_header("mem-b", "semantic", "semantic info"),
        ]
        text, alias_map = build_manifest(headers)
        assert 0 in alias_map
        assert 1 in alias_map
        assert "[0]" in text
        assert "[1]" in text

    def test_grouped_by_category(self) -> None:
        headers = [
            _make_header("a", "profile", "profile"),
            _make_header("b", "semantic", "semantic"),
            _make_header("c", "profile", "another profile"),
        ]
        text, _ = build_manifest(headers)
        assert "## profile" in text
        assert "## semantic" in text

    def test_empty(self) -> None:
        text, alias_map = build_manifest([])
        assert alias_map == {}


class TestRelevanceSelectorRanking:
    def test_ranking_formula(self) -> None:
        """Verify the ranking formula components."""
        h = _make_header(access_count=5, age_days=15, source="user", category="episodic")
        sm = ScoredMemory(header=h, relevance=4, reason="test", final_score=0.0)

        # Manual calculation
        salience = math.log(5 + 2)
        time_decay = math.exp(-0.693 * 15 / 30)
        source_weight = 1.0  # user
        expected = 4 * salience * time_decay * source_weight

        # Use selector's internal ranking
        from kernel.memory.index import MemoryIndex

        idx = MemoryIndex()
        selector = RelevanceSelector(memory_index=idx)
        ranked = selector._rank([sm])
        assert ranked[0].final_score == pytest.approx(expected, rel=0.01)

    def test_evergreen_no_decay_in_ranking(self) -> None:
        """Profile memories should not be penalized by age in ranking."""
        h_young = _make_header(category="profile", age_days=0, access_count=1, source="agent")
        h_old = _make_header(category="profile", age_days=100, access_count=1, source="agent")

        sm_young = ScoredMemory(header=h_young, relevance=3, reason="", final_score=0.0)
        sm_old = ScoredMemory(header=h_old, relevance=3, reason="", final_score=0.0)

        from kernel.memory.index import MemoryIndex

        idx = MemoryIndex()
        selector = RelevanceSelector(memory_index=idx)
        selector._rank([sm_young, sm_old])

        # Evergreen: both should have same final_score
        assert sm_young.final_score == pytest.approx(sm_old.final_score, rel=0.01)

    def test_higher_access_ranks_higher(self) -> None:
        h_low = _make_header(access_count=0)
        h_high = _make_header(access_count=20)

        sm_low = ScoredMemory(header=h_low, relevance=3, reason="", final_score=0.0)
        sm_high = ScoredMemory(header=h_high, relevance=3, reason="", final_score=0.0)

        from kernel.memory.index import MemoryIndex

        idx = MemoryIndex()
        selector = RelevanceSelector(memory_index=idx)
        selector._rank([sm_low, sm_high])

        assert sm_high.final_score > sm_low.final_score
