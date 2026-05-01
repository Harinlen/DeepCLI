"""Tests for project-level memory (Phase 5.7C).

Covers: project memory store with restricted types, scope parameter
in memory tools, dual index injection, and permission engine protection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.extensions.tools.base import ToolContext
from daemon.extensions.tools.builtin.memory_write import MemoryWriteTool
from daemon.extensions.tools.builtin.memory_list import MemoryListTool
from daemon.extensions.tools.builtin.memory_delete import MemoryDeleteTool
from daemon.memory.schema import (
    PROJECT_TYPES,
    MemoryFrontmatter,
    MemoryScope,
    MemoryType,
)
from daemon.memory.store import MemoryStore, MemoryStoreError


# ------------------------------------------------------------------
# MemoryStore scoping
# ------------------------------------------------------------------


class TestProjectMemoryStore:
    """Tests for project-scoped MemoryStore."""

    def test_project_store_creates_task_context_dirs(self, tmp_path: Path) -> None:
        store = MemoryStore(
            root=tmp_path / "mem",
            scope=MemoryScope.PROJECT,
            allowed_types=PROJECT_TYPES,
        )
        store.load()
        assert (tmp_path / "mem" / "task").is_dir()
        assert (tmp_path / "mem" / "context").is_dir()
        # Global dirs should not exist.
        assert not (tmp_path / "mem" / "user").exists()
        assert not (tmp_path / "mem" / "feedback").exists()

    def test_project_store_rejects_global_types(self, tmp_path: Path) -> None:
        store = MemoryStore(
            root=tmp_path / "mem",
            scope=MemoryScope.PROJECT,
            allowed_types=PROJECT_TYPES,
        )
        store.load()
        fm = MemoryFrontmatter(name="test", description="test desc", type=MemoryType.USER)
        with pytest.raises(MemoryStoreError, match="not allowed"):
            store.write(MemoryType.USER, "test.md", fm, "body")

    def test_project_store_accepts_task_type(self, tmp_path: Path) -> None:
        store = MemoryStore(
            root=tmp_path / "mem",
            scope=MemoryScope.PROJECT,
            allowed_types=PROJECT_TYPES,
        )
        store.load()
        fm = MemoryFrontmatter(name="current", description="refactoring auth", type=MemoryType.TASK)
        path = store.write(MemoryType.TASK, "current.md", fm, "body")
        assert path.exists()
        records = store.records()
        assert len(records) == 1
        assert records[0].frontmatter.type == MemoryType.TASK

    def test_global_store_rejects_project_types(self, tmp_path: Path) -> None:
        store = MemoryStore(root=tmp_path / "mem")  # default = global
        store.load()
        fm = MemoryFrontmatter(name="test", description="test", type=MemoryType.TASK)
        with pytest.raises(MemoryStoreError, match="not allowed"):
            store.write(MemoryType.TASK, "test.md", fm, "body")


# ------------------------------------------------------------------
# Memory tools with scope parameter
# ------------------------------------------------------------------


def _make_ctx(
    tmp_path: Path,
    *,
    with_project: bool = True,
) -> ToolContext:
    """Build a ToolContext with both memory stores."""
    global_store = MemoryStore(tmp_path / "global_mem")
    global_store.load()

    project_store = None
    if with_project:
        project_store = MemoryStore(
            tmp_path / "project_mem",
            scope=MemoryScope.PROJECT,
            allowed_types=PROJECT_TYPES,
        )
        project_store.load()

    return ToolContext(
        cwd=str(tmp_path),
        memory_store=global_store,
        project_memory_store=project_store,
    )


class TestMemoryToolsScope:
    """Tests for scope parameter in memory tools."""

    @pytest.mark.asyncio
    async def test_write_to_project_scope(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        tool = MemoryWriteTool()
        result = await tool.execute(
            {
                "scope": "project",
                "type": "task",
                "filename": "current.md",
                "name": "current task",
                "description": "refactoring auth middleware",
                "kind": "standalone",
                "body": "Working on auth refactor.",
            },
            ctx,
        )
        assert not result.is_error
        assert "Wrote memory" in result.output

    @pytest.mark.asyncio
    async def test_write_global_default(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        tool = MemoryWriteTool()
        result = await tool.execute(
            {
                "type": "user",
                "filename": "role.md",
                "name": "role",
                "description": "backend engineer",
                "body": "I am a backend engineer.",
            },
            ctx,
        )
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_write_project_unavailable(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, with_project=False)
        tool = MemoryWriteTool()
        result = await tool.execute(
            {
                "scope": "project",
                "type": "task",
                "filename": "test.md",
                "name": "test",
                "description": "test",
                "body": "body",
            },
            ctx,
        )
        assert result.is_error
        assert "not available" in result.output

    @pytest.mark.asyncio
    async def test_list_shows_both_scopes(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        # Write one in each scope.
        assert ctx.memory_store is not None
        assert ctx.project_memory_store is not None
        ctx.memory_store.write(
            MemoryType.USER,
            "role.md",
            MemoryFrontmatter(name="role", description="eng", type=MemoryType.USER),
            "body",
        )
        ctx.project_memory_store.write(
            MemoryType.TASK,
            "current.md",
            MemoryFrontmatter(name="current", description="auth", type=MemoryType.TASK),
            "body",
        )

        tool = MemoryListTool()
        result = await tool.execute({}, ctx)
        assert not result.is_error
        import json

        entries = json.loads(result.output)
        scopes = {e["scope"] for e in entries}
        assert "global" in scopes
        assert "project" in scopes

    @pytest.mark.asyncio
    async def test_delete_project_scope(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        assert ctx.project_memory_store is not None
        ctx.project_memory_store.write(
            MemoryType.TASK,
            "todo.md",
            MemoryFrontmatter(name="todo", description="tasks", type=MemoryType.TASK),
            "body",
        )
        tool = MemoryDeleteTool()
        result = await tool.execute(
            {"scope": "project", "type": "task", "filename": "todo.md"}, ctx
        )
        assert not result.is_error
        assert "Deleted" in result.output


# ------------------------------------------------------------------
# Permission engine protection
# ------------------------------------------------------------------


class TestProjectMemoryPermission:
    """Tests that file_edit/file_write is denied on project memory."""

    def test_project_memory_path_denied(self, tmp_path: Path) -> None:
        from daemon.permissions.engine import _is_memory_write_violation

        project_memory_file = str(tmp_path / ".mustang" / "memory" / "task" / "current.md")
        assert _is_memory_write_violation("file_edit", {"file_path": project_memory_file})
        assert _is_memory_write_violation("file_write", {"file_path": project_memory_file})

    def test_non_memory_path_allowed(self, tmp_path: Path) -> None:
        from daemon.permissions.engine import _is_memory_write_violation

        normal_file = str(tmp_path / "src" / "main.py")
        assert not _is_memory_write_violation("file_edit", {"file_path": normal_file})
