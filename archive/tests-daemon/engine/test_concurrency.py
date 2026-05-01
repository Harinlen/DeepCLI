"""Tests for the tool-call concurrency planner.

Verifies that :func:`plan_execution_groups` correctly partitions tool
calls into ordered groups based on concurrency hints, conflict keys,
and permission pre-approval status.
"""

from __future__ import annotations

from daemon.extensions.tools.base import ConcurrencyHint
from daemon.engine.orchestrator.concurrency import (
    ExecutionSlot,
    plan_execution_groups,
)
from daemon.providers.base import ToolUseContent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tc(name: str, call_id: str = "") -> ToolUseContent:
    """Create a minimal ToolUseContent for testing."""
    return ToolUseContent(
        tool_call_id=call_id or f"call_{name}",
        name=name,
        arguments={},
    )


def _slot(
    name: str,
    hint: ConcurrencyHint = ConcurrencyHint.PARALLEL,
    key: str | None = None,
    pre_approved: bool = True,
    call_id: str = "",
) -> ExecutionSlot:
    """Shorthand for building an ExecutionSlot."""
    return ExecutionSlot(
        tc=_tc(name, call_id),
        hint=hint,
        key=key,
        pre_approved=pre_approved,
    )


def _names(groups: list[list[ExecutionSlot]]) -> list[list[str]]:
    """Extract tool names from groups for easy assertion."""
    return [[s.tc.name for s in group] for group in groups]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlanExecutionGroups:
    """Unit tests for ``plan_execution_groups``."""

    def test_empty_input(self) -> None:
        assert plan_execution_groups([]) == []

    def test_single_parallel(self) -> None:
        groups = plan_execution_groups([_slot("file_read")])
        assert _names(groups) == [["file_read"]]

    def test_single_serial(self) -> None:
        groups = plan_execution_groups(
            [
                _slot("bash", hint=ConcurrencyHint.SERIAL),
            ]
        )
        assert _names(groups) == [["bash"]]

    def test_all_parallel_one_group(self) -> None:
        """Multiple PARALLEL tools land in a single group."""
        slots = [
            _slot("file_read"),
            _slot("glob"),
            _slot("grep"),
        ]
        groups = plan_execution_groups(slots)
        assert _names(groups) == [["file_read", "glob", "grep"]]

    def test_serial_breaks_group(self) -> None:
        """A SERIAL tool flushes the current group and runs alone."""
        slots = [
            _slot("file_read"),
            _slot("glob"),
            _slot("bash", hint=ConcurrencyHint.SERIAL),
            _slot("grep"),
        ]
        groups = plan_execution_groups(slots)
        assert _names(groups) == [
            ["file_read", "glob"],
            ["bash"],
            ["grep"],
        ]

    def test_keyed_no_conflict(self) -> None:
        """KEYED tools with different keys share a group."""
        slots = [
            _slot("file_write", hint=ConcurrencyHint.KEYED, key="/a.txt"),
            _slot("file_write", hint=ConcurrencyHint.KEYED, key="/b.txt", call_id="call_fw2"),
        ]
        groups = plan_execution_groups(slots)
        assert _names(groups) == [["file_write", "file_write"]]

    def test_keyed_conflict_splits(self) -> None:
        """KEYED tools with the same key go to separate groups."""
        slots = [
            _slot("file_edit", hint=ConcurrencyHint.KEYED, key="/a.txt"),
            _slot("file_edit", hint=ConcurrencyHint.KEYED, key="/a.txt", call_id="call_fe2"),
        ]
        groups = plan_execution_groups(slots)
        assert _names(groups) == [["file_edit"], ["file_edit"]]

    def test_keyed_mixed_with_parallel(self) -> None:
        """PARALLEL and non-conflicting KEYED tools share a group."""
        slots = [
            _slot("file_read"),
            _slot("file_write", hint=ConcurrencyHint.KEYED, key="/a.txt"),
            _slot("glob"),
        ]
        groups = plan_execution_groups(slots)
        assert _names(groups) == [["file_read", "file_write", "glob"]]

    def test_not_pre_approved_is_serial(self) -> None:
        """Tools without pre-approval always get their own group."""
        slots = [
            _slot("file_read"),
            _slot("web_fetch", pre_approved=False),
            _slot("glob"),
        ]
        groups = plan_execution_groups(slots)
        assert _names(groups) == [
            ["file_read"],
            ["web_fetch"],
            ["glob"],
        ]

    def test_all_serial(self) -> None:
        """All SERIAL tools each get their own singleton group."""
        slots = [
            _slot("bash", hint=ConcurrencyHint.SERIAL),
            _slot("enter_plan_mode", hint=ConcurrencyHint.SERIAL),
            _slot("todo_write", hint=ConcurrencyHint.SERIAL),
        ]
        groups = plan_execution_groups(slots)
        assert _names(groups) == [["bash"], ["enter_plan_mode"], ["todo_write"]]

    def test_keyed_none_key_treated_as_conflict(self) -> None:
        """KEYED tool with key=None cannot join a group (self-conflicts)."""
        slots = [
            _slot("file_write", hint=ConcurrencyHint.KEYED, key=None),
        ]
        groups = plan_execution_groups(slots)
        # key=None + KEYED → can_accept returns False → singleton
        assert _names(groups) == [["file_write"]]

    def test_complex_interleaving(self) -> None:
        """Realistic scenario: reads, then write, then serial, then reads."""
        slots = [
            _slot("file_read"),
            _slot("grep"),
            _slot("file_write", hint=ConcurrencyHint.KEYED, key="/x.py"),
            _slot("file_edit", hint=ConcurrencyHint.KEYED, key="/y.py"),
            _slot("bash", hint=ConcurrencyHint.SERIAL),
            _slot("file_read", call_id="call_fr2"),
            _slot("glob", call_id="call_glob2"),
        ]
        groups = plan_execution_groups(slots)
        assert _names(groups) == [
            ["file_read", "grep", "file_write", "file_edit"],
            ["bash"],
            ["file_read", "glob"],
        ]

    def test_keyed_conflict_after_parallel(self) -> None:
        """Key conflict flushes and starts a new group that can grow."""
        slots = [
            _slot("file_read"),
            _slot("file_write", hint=ConcurrencyHint.KEYED, key="/a.txt"),
            _slot("file_edit", hint=ConcurrencyHint.KEYED, key="/a.txt"),
            _slot("glob"),
        ]
        groups = plan_execution_groups(slots)
        assert _names(groups) == [
            ["file_read", "file_write"],
            ["file_edit", "glob"],
        ]

    def test_multiple_not_pre_approved_consecutive(self) -> None:
        """Consecutive non-pre-approved tools each get singleton groups."""
        slots = [
            _slot("web_fetch", pre_approved=False),
            _slot("web_search", pre_approved=False),
        ]
        groups = plan_execution_groups(slots)
        assert _names(groups) == [["web_fetch"], ["web_search"]]


class TestBuiltinToolConcurrencyHints:
    """Verify builtin tools declare the correct concurrency hints."""

    def test_parallel_tools(self) -> None:
        """Read-only and network-read tools are PARALLEL."""
        from daemon.extensions.tools.builtin.file_read import FileReadTool
        from daemon.extensions.tools.builtin.glob_tool import GlobTool
        from daemon.extensions.tools.builtin.grep_tool import GrepTool
        from daemon.extensions.tools.builtin.http_fetch import HttpFetchTool
        from daemon.extensions.tools.builtin.memory_list import MemoryListTool
        from daemon.extensions.tools.builtin.web_search import WebSearchTool

        for cls in (FileReadTool, GlobTool, GrepTool, MemoryListTool, HttpFetchTool, WebSearchTool):
            assert cls.concurrency is ConcurrencyHint.PARALLEL, f"{cls.name} should be PARALLEL"

    def test_keyed_tools(self) -> None:
        """File write and memory write tools are KEYED."""
        from daemon.extensions.tools.builtin.file_write import FileWriteTool
        from daemon.extensions.tools.builtin.file_edit import FileEditTool
        from daemon.extensions.tools.builtin.memory_write import MemoryWriteTool
        from daemon.extensions.tools.builtin.memory_append import MemoryAppendTool
        from daemon.extensions.tools.builtin.memory_delete import MemoryDeleteTool

        for cls in (
            FileWriteTool,
            FileEditTool,
            MemoryWriteTool,
            MemoryAppendTool,
            MemoryDeleteTool,
        ):
            assert cls.concurrency is ConcurrencyHint.KEYED, f"{cls.name} should be KEYED"

    def test_serial_tools(self) -> None:
        """Bash and state-mutating tools remain SERIAL (default)."""
        from daemon.extensions.tools.builtin.bash import BashTool
        from daemon.extensions.tools.builtin.todo_write import TodoWriteTool
        from daemon.extensions.tools.builtin.enter_plan_mode import EnterPlanModeTool
        from daemon.extensions.tools.builtin.exit_plan_mode import ExitPlanModeTool

        for cls in (BashTool, TodoWriteTool, EnterPlanModeTool, ExitPlanModeTool):
            assert cls.concurrency is ConcurrencyHint.SERIAL, f"{cls.name} should be SERIAL"

    def test_keyed_tools_return_keys(self) -> None:
        """KEYED tools return meaningful keys from concurrency_key()."""
        from daemon.extensions.tools.builtin.file_write import FileWriteTool
        from daemon.extensions.tools.builtin.file_edit import FileEditTool
        from daemon.extensions.tools.builtin.memory_write import MemoryWriteTool
        from daemon.extensions.tools.builtin.memory_append import MemoryAppendTool
        from daemon.extensions.tools.builtin.memory_delete import MemoryDeleteTool

        fw = FileWriteTool()
        assert fw.concurrency_key({"file_path": "/tmp/a.txt"}) == "/tmp/a.txt"

        fe = FileEditTool()
        assert fe.concurrency_key({"file_path": "/tmp/b.txt"}) == "/tmp/b.txt"

        mw = MemoryWriteTool()
        assert mw.concurrency_key({"filename": "role.md"}) == "project:role.md"
        assert mw.concurrency_key({"filename": "role.md", "scope": "global"}) == "global:role.md"

        ma = MemoryAppendTool()
        assert ma.concurrency_key({"filename": "log.md"}) == "project:log.md"

        md = MemoryDeleteTool()
        assert md.concurrency_key({"filename": "old.md"}) == "project:old.md"

    def test_parallel_tools_return_none_key(self) -> None:
        """PARALLEL tools' default concurrency_key returns None."""
        from daemon.extensions.tools.builtin.file_read import FileReadTool

        fr = FileReadTool()
        assert fr.concurrency_key({"file_path": "/tmp/x"}) is None
