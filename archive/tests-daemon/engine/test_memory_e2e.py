"""End-to-end test: memory flowing through the full Orchestrator loop.

Verifies:
  1. LLM calls memory_write via tool loop → MemoryStore writes file.
  2. Store updates index.md on disk.
  3. A fresh MemoryStore over the same root (simulating daemon
     restart) sees the written entry.
  4. The memory index appears in the second session's system prompt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from daemon.engine.orchestrator import Orchestrator
from daemon.engine.stream import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCallResult,
    ToolCallStart,
    UsageInfo,
)
from daemon.extensions.tools.builtin.memory_list import MemoryListTool
from daemon.extensions.tools.builtin.memory_write import MemoryWriteTool
from daemon.extensions.tools.registry import ToolRegistry
from daemon.memory.store import MemoryStore
from daemon.providers.base import Message, ModelInfo, Provider, ToolDefinition


class _ScriptedProvider(Provider):
    """Provider that yields a pre-scripted sequence of events per call."""

    name = "scripted"

    def __init__(self, events_sequence: list[list[StreamEvent]]) -> None:
        self._seq = events_sequence
        self._i = 0

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        events = self._seq[self._i]
        self._i += 1
        for ev in events:
            yield ev

    async def models(self) -> list[ModelInfo]:
        return []

    async def query_context_window(self) -> int | None:
        return None


def _make_orchestrator(
    tmp_cwd: Path,
    memory_store: MemoryStore,
    events_sequence: list[list[StreamEvent]],
) -> Orchestrator:
    from tests.daemon.engine.orchestrator_test_utils import make_test_orchestrator

    tool_registry = ToolRegistry()
    tool_registry.register(MemoryWriteTool())
    tool_registry.register(MemoryListTool())

    return make_test_orchestrator(
        provider=_ScriptedProvider(events_sequence),
        tmp_path=tmp_cwd,
        tool_registry=tool_registry,
        memory_store=memory_store,
    )


async def _drain(orch: Orchestrator, text: str) -> list[StreamEvent]:
    out: list[StreamEvent] = []
    async for ev in orch.query(text):
        out.append(ev)
    return out


@pytest.mark.asyncio
async def test_memory_write_roundtrip_through_orchestrator(
    tmp_path: Path,
) -> None:
    """LLM → memory_write tool call → MemoryStore writes file → restart
    sees it → system prompt of next session contains the entry.
    """
    memory_root = tmp_path / "memory"
    store_a = MemoryStore(memory_root)
    store_a.load()
    assert store_a.records() == []

    # Script: turn 1 → assistant calls memory_write; turn 2 → just ends.
    script = [
        [
            ToolCallStart(
                tool_call_id="t1",
                tool_name="memory_write",
                arguments={
                    "type": "user",
                    "filename": "role.md",
                    "name": "role",
                    "description": "TrueNorth backend engineer, Go expert",
                    "kind": "standalone",
                    "body": "Backend engineer at TrueNorth.",
                },
            ),
            StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=5)),
        ],
        [
            TextDelta(content="Memory saved."),
            StreamEnd(usage=UsageInfo(input_tokens=5, output_tokens=2)),
        ],
    ]

    orch = _make_orchestrator(tmp_path, store_a, script)
    events = await _drain(orch, "I'm a backend engineer at TrueNorth")

    # Tool call completed successfully.
    results = [e for e in events if isinstance(e, ToolCallResult)]
    assert len(results) == 1
    assert results[0].is_error is False
    assert "role.md" in results[0].output

    # Store A reflects the write immediately.
    assert len(store_a.records()) == 1
    assert store_a.records()[0].frontmatter.name == "role"

    # Simulate daemon restart: fresh store over the same directory.
    store_b = MemoryStore(memory_root)
    store_b.load()
    recs = store_b.records()
    assert len(recs) == 1
    assert recs[0].frontmatter.description == "TrueNorth backend engineer, Go expert"

    # Second session: system prompt includes the memory index.
    index = store_b.index_text()
    assert "role.md" in index
    assert "TrueNorth backend engineer" in index

    # log.md also captured the write.
    log_text = store_b.log.read()
    assert "WRITE" in log_text
    assert "user/role.md" in log_text


@pytest.mark.asyncio
async def test_system_prompt_sees_memory_across_turns(
    tmp_path: Path,
) -> None:
    """Orchestrator's per-round system prompt pulls fresh index every turn."""
    memory_root = tmp_path / "memory"
    store = MemoryStore(memory_root)
    store.load()

    script = [
        [
            TextDelta(content="ok"),
            StreamEnd(usage=UsageInfo(input_tokens=1, output_tokens=1)),
        ]
    ]
    orch = _make_orchestrator(tmp_path, store, script)
    from daemon.engine.context import prompt_sections_to_text

    # First call — no memory yet
    sections1 = await orch.prompt_builder.build_for_round(
        model="m",
        skill_info=None,
        memory_manager=orch.memory_manager,
        plan_mode=orch.plan_mode,
    )
    prompt1 = prompt_sections_to_text(sections1)
    assert "## User" not in prompt1  # empty index

    # Populate store out-of-band
    from daemon.memory.schema import MemoryFrontmatter, MemoryType

    store.write(
        MemoryType.USER,
        "role.md",
        MemoryFrontmatter(name="role", description="be engineer", type=MemoryType.USER),
        "body",
    )

    # Second call — memory index now includes the new entry
    sections2 = await orch.prompt_builder.build_for_round(
        model="m",
        skill_info=None,
        memory_manager=orch.memory_manager,
        plan_mode=orch.plan_mode,
    )
    prompt2 = prompt_sections_to_text(sections2)
    assert "## User" in prompt2
    assert "be engineer" in prompt2
    assert "role.md" in prompt2
