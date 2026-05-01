"""Integration tests for the 4 memory builtin tools."""

import json
from pathlib import Path

import pytest

from daemon.extensions.tools.base import ToolContext
from daemon.extensions.tools.builtin.memory_append import MemoryAppendTool
from daemon.extensions.tools.builtin.memory_delete import MemoryDeleteTool
from daemon.extensions.tools.builtin.memory_list import MemoryListTool
from daemon.extensions.tools.builtin.memory_write import MemoryWriteTool
from daemon.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(tmp_path / "memory")
    s.load()
    return s


@pytest.fixture
def ctx(store: MemoryStore) -> ToolContext:
    return ToolContext(cwd=str(store.root), memory_store=store)


class TestMemoryWriteTool:
    async def test_write_standalone(self, ctx: ToolContext, store: MemoryStore) -> None:
        tool = MemoryWriteTool()
        result = await tool.execute(
            {
                "type": "user",
                "filename": "role.md",
                "name": "role",
                "description": "backend engineer",
                "kind": "standalone",
                "body": "Full body.",
            },
            ctx,
        )
        assert result.is_error is False
        assert "role.md" in result.output
        assert len(store.records()) == 1

    async def test_write_aggregate(self, ctx: ToolContext, store: MemoryStore) -> None:
        tool = MemoryWriteTool()
        result = await tool.execute(
            {
                "type": "user",
                "filename": "prefs.md",
                "name": "prefs",
                "description": "pytest, tabs, ripgrep",
                "kind": "aggregate",
                "body": "## Tools\n- pytest\n",
            },
            ctx,
        )
        assert result.is_error is False
        recs = store.records()
        assert recs[0].frontmatter.kind.value == "aggregate"

    async def test_write_default_kind_standalone(
        self, ctx: ToolContext, store: MemoryStore
    ) -> None:
        tool = MemoryWriteTool()
        await tool.execute(
            {
                "type": "feedback",
                "filename": "x.md",
                "name": "x",
                "description": "y",
                "body": "b",
            },
            ctx,
        )
        recs = store.records()
        assert recs[0].frontmatter.kind.value == "standalone"

    async def test_write_body_too_long(self, ctx: ToolContext) -> None:
        tool = MemoryWriteTool()
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            await tool.execute(
                {
                    "type": "user",
                    "filename": "x.md",
                    "name": "x",
                    "description": "y",
                    "body": "a" * 20_001,
                },
                ctx,
            )

    async def test_write_bad_filename(self, ctx: ToolContext) -> None:
        tool = MemoryWriteTool()
        result = await tool.execute(
            {
                "type": "user",
                "filename": "../escape.md",
                "name": "x",
                "description": "y",
                "body": "b",
            },
            ctx,
        )
        assert result.is_error is True

    async def test_no_store(self, tmp_path: Path) -> None:
        tool = MemoryWriteTool()
        ctx = ToolContext(cwd=str(tmp_path), memory_store=None)
        result = await tool.execute(
            {
                "type": "user",
                "filename": "x.md",
                "name": "x",
                "description": "y",
                "body": "b",
            },
            ctx,
        )
        assert result.is_error is True
        assert "not available" in result.output


class TestMemoryAppendTool:
    async def test_append_to_aggregate(self, ctx: ToolContext, store: MemoryStore) -> None:
        await MemoryWriteTool().execute(
            {
                "type": "user",
                "filename": "p.md",
                "name": "p",
                "description": "x",
                "kind": "aggregate",
                "body": "## Tools\n- pytest\n",
            },
            ctx,
        )
        result = await MemoryAppendTool().execute(
            {
                "type": "user",
                "filename": "p.md",
                "section": "Tools",
                "bullet": "ripgrep",
            },
            ctx,
        )
        assert result.is_error is False
        _, body = store.read("user/p.md")
        assert "ripgrep" in body

    async def test_append_to_standalone_fails(self, ctx: ToolContext) -> None:
        await MemoryWriteTool().execute(
            {
                "type": "user",
                "filename": "r.md",
                "name": "r",
                "description": "x",
                "kind": "standalone",
                "body": "body",
            },
            ctx,
        )
        result = await MemoryAppendTool().execute(
            {
                "type": "user",
                "filename": "r.md",
                "section": "x",
                "bullet": "y",
            },
            ctx,
        )
        assert result.is_error is True


class TestMemoryDeleteTool:
    async def test_delete_existing(self, ctx: ToolContext, store: MemoryStore) -> None:
        await MemoryWriteTool().execute(
            {
                "type": "user",
                "filename": "r.md",
                "name": "r",
                "description": "x",
                "body": "b",
            },
            ctx,
        )
        result = await MemoryDeleteTool().execute({"type": "user", "filename": "r.md"}, ctx)
        assert "Deleted" in result.output
        assert store.records() == []

    async def test_delete_missing(self, ctx: ToolContext) -> None:
        result = await MemoryDeleteTool().execute({"type": "user", "filename": "nope.md"}, ctx)
        assert "Not found" in result.output
        assert result.is_error is False


class TestMemoryListTool:
    async def test_list_empty(self, ctx: ToolContext) -> None:
        result = await MemoryListTool().execute({}, ctx)
        assert result.is_error is False
        assert json.loads(result.output) == []

    async def test_list_with_entries(self, ctx: ToolContext) -> None:
        for t, f, n in [
            ("user", "role.md", "role"),
            ("feedback", "testing.md", "testing"),
        ]:
            await MemoryWriteTool().execute(
                {
                    "type": t,
                    "filename": f,
                    "name": n,
                    "description": "x",
                    "body": "b",
                },
                ctx,
            )
        result = await MemoryListTool().execute({}, ctx)
        parsed = json.loads(result.output)
        assert len(parsed) == 2
        names = [p["name"] for p in parsed]
        assert "role" in names
        assert "testing" in names

    async def test_list_filter_by_type(self, ctx: ToolContext) -> None:
        for t, f, n in [
            ("user", "a.md", "a"),
            ("feedback", "b.md", "b"),
            ("user", "c.md", "c"),
        ]:
            await MemoryWriteTool().execute(
                {"type": t, "filename": f, "name": n, "description": "x", "body": "b"},
                ctx,
            )
        result = await MemoryListTool().execute({"type": "user"}, ctx)
        parsed = json.loads(result.output)
        assert len(parsed) == 2
        assert all(p["type"] == "user" for p in parsed)

    async def test_list_fields_present(self, ctx: ToolContext) -> None:
        await MemoryWriteTool().execute(
            {
                "type": "user",
                "filename": "r.md",
                "name": "r",
                "description": "backend engineer",
                "kind": "aggregate",
                "body": "b",
            },
            ctx,
        )
        result = await MemoryListTool().execute({}, ctx)
        entry = json.loads(result.output)[0]
        assert entry["relative"] == "user/r.md"
        assert entry["name"] == "r"
        assert entry["description"] == "backend engineer"
        assert entry["type"] == "user"
        assert entry["kind"] == "aggregate"
