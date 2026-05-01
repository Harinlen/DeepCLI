"""Tests for memory.background — extraction mutual exclusion and consolidation."""

from __future__ import annotations

from pathlib import Path

import pytest

from kernel.memory.background import BackgroundMemoryAgent
from kernel.memory.index import MemoryIndex
from kernel.memory.store import ensure_directory_tree, write_memory
from kernel.memory.types import MemoryHeader


@pytest.fixture()
def mem_root(tmp_path: Path) -> Path:
    root = tmp_path / "memory"
    ensure_directory_tree(root)
    return root


@pytest.fixture()
async def index(mem_root: Path) -> MemoryIndex:
    idx = MemoryIndex()
    await idx.load(mem_root)
    return idx


def _make_header(filename: str, category: str = "semantic") -> MemoryHeader:
    from datetime import datetime, timezone

    return MemoryHeader(
        filename=filename,
        name=filename,
        description=f"description of {filename}",
        category=category,  # type: ignore[arg-type]
        source="agent",
        created=datetime.now(timezone.utc),
        updated=datetime.now(timezone.utc),
        access_count=0,
        locked=False,
        rel_path=f"{category}/{filename}.md",
    )


class TestMutualExclusion:
    @pytest.mark.anyio()
    async def test_skip_after_main_agent_write(self, mem_root: Path, index: MemoryIndex) -> None:
        agent = BackgroundMemoryAgent(
            memory_index=index,
            global_root=mem_root,
            project_root=None,
        )

        # Simulate main agent writing memory
        agent.notify_main_agent_write()

        # Pre-compact flush should be skipped
        messages = [{"role": "user", "content": "important fact"}]
        await agent.on_pre_compact(messages)

        # No new files should be created (skipped due to mutual exclusion)
        from kernel.memory.store import scan_headers

        headers = scan_headers(mem_root)
        assert len(headers) == 0

    def test_turn_end_resets_flag(self, mem_root: Path, index: MemoryIndex) -> None:
        agent = BackgroundMemoryAgent(
            memory_index=index,
            global_root=mem_root,
            project_root=None,
        )
        agent.notify_main_agent_write()
        assert agent._main_agent_wrote_this_turn is True
        agent.on_turn_end()
        assert agent._main_agent_wrote_this_turn is False

    def test_turn_count_increments(self, mem_root: Path, index: MemoryIndex) -> None:
        agent = BackgroundMemoryAgent(
            memory_index=index,
            global_root=mem_root,
            project_root=None,
        )
        assert agent._turn_count == 0
        agent.on_turn_end()
        assert agent._turn_count == 1
        agent.on_turn_end()
        assert agent._turn_count == 2


class TestBackgroundLifecycle:
    @pytest.mark.anyio()
    async def test_start_stop(self, mem_root: Path, index: MemoryIndex) -> None:
        agent = BackgroundMemoryAgent(
            memory_index=index,
            global_root=mem_root,
            project_root=None,
        )
        agent.start()
        assert agent._task is not None
        await agent.stop(timeout=2.0)
        assert agent._task is None

    @pytest.mark.anyio()
    async def test_stop_without_start(self, mem_root: Path, index: MemoryIndex) -> None:
        agent = BackgroundMemoryAgent(
            memory_index=index,
            global_root=mem_root,
            project_root=None,
        )
        await agent.stop()  # should not raise


class TestConsolidation:
    @pytest.mark.anyio()
    async def test_consolidation_runs_without_llm(self, mem_root: Path, index: MemoryIndex) -> None:
        """Consolidation should complete even without an LLM provider."""
        agent = BackgroundMemoryAgent(
            memory_index=index,
            global_root=mem_root,
            project_root=None,
            llm_provider=None,
        )
        # Create some memories
        write_memory(mem_root, "semantic", _make_header("a"), "body a")
        write_memory(mem_root, "semantic", _make_header("b"), "body b")
        index.invalidate()

        # Should not raise
        await agent.run_consolidation()
