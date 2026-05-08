from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kernel.tools.repl.linter import ReplLintError, lint_repl_code
from kernel.tools.repl.runner import ReplRunner
from kernel.tools.types import NestedToolResult


def test_linter_rejects_import() -> None:
    with pytest.raises(ReplLintError, match="import"):
        lint_repl_code("import os")


def test_linter_rejects_while() -> None:
    with pytest.raises(ReplLintError, match="while"):
        lint_repl_code("while True:\n    pass")


def test_linter_rejects_dunder_escape() -> None:
    with pytest.raises(ReplLintError, match="__class__"):
        lint_repl_code("x = ().__class__")


@pytest.mark.asyncio
async def test_runner_persists_worker_globals(tmp_path: Path) -> None:
    runner = ReplRunner()
    try:
        async def run_tool(name: str, input: dict[str, object]) -> NestedToolResult:
            return NestedToolResult(tool_name=name, text="unused")

        first = await runner.run(
            session_id="s",
            cwd=tmp_path,
            code="x = 41\no = x + 1",
            run_tool=run_tool,
            timeout_ms=10_000,
        )
        assert first.error is None
        assert first.value == 42

        second = await runner.run(
            session_id="s",
            cwd=tmp_path,
            code="o = x + 2",
            run_tool=run_tool,
            timeout_ms=10_000,
        )
        assert second.error is None
        assert second.value == 43
    finally:
        await runner.shutdown_all()


@pytest.mark.asyncio
async def test_runner_bridges_nested_tool_call(tmp_path: Path) -> None:
    runner = ReplRunner()
    seen: list[tuple[str, dict[str, object]]] = []
    try:
        async def run_tool(name: str, input: dict[str, object]) -> NestedToolResult:
            seen.append((name, input))
            return NestedToolResult(tool_name=name, text="hello from read")

        result = await runner.run(
            session_id="s",
            cwd=tmp_path,
            code='o = await Read(file_path="README.md")',
            run_tool=run_tool,
            timeout_ms=10_000,
        )
        assert result.error is None
        assert result.value == "hello from read"
        assert seen == [("Read", {"file_path": "README.md", "__repl_cwd": str(tmp_path)})]
    finally:
        await runner.shutdown_all()


@pytest.mark.asyncio
async def test_runner_helpers_accept_positional_shorthand(tmp_path: Path) -> None:
    runner = ReplRunner()
    seen: list[tuple[str, dict[str, object]]] = []
    try:
        async def run_tool(name: str, input: dict[str, object]) -> NestedToolResult:
            seen.append((name, input))
            return NestedToolResult(tool_name=name, text=f"{name} ok")

        result = await runner.run(
            session_id="s",
            cwd=tmp_path,
            code=(
                'a = await Read("/etc/hostname")\n'
                'b = await Bash("printf hi")\n'
                'c = await Grep("needle", path="src")\n'
                "o = [a, b, c]"
            ),
            run_tool=run_tool,
            timeout_ms=10_000,
        )
        assert result.error is None
        assert result.value == ["Read ok", "Bash ok", "Grep ok"]
        assert seen == [
            ("Read", {"file_path": "/etc/hostname", "__repl_cwd": str(tmp_path)}),
            ("Bash", {"command": "printf hi", "__repl_cwd": str(tmp_path)}),
            (
                "Grep",
                {"path": "src", "pattern": "needle", "__repl_cwd": str(tmp_path)},
            ),
        ]
    finally:
        await runner.shutdown_all()


@pytest.mark.asyncio
async def test_runner_auto_awaits_last_helper_expression(tmp_path: Path) -> None:
    runner = ReplRunner()
    seen: list[tuple[str, dict[str, object]]] = []
    try:
        async def run_tool(name: str, input: dict[str, object]) -> NestedToolResult:
            seen.append((name, input))
            return NestedToolResult(tool_name=name, text="host\n")

        result = await runner.run(
            session_id="s",
            cwd=tmp_path,
            code='Read("/etc/hostname")',
            run_tool=run_tool,
            timeout_ms=10_000,
        )
        assert result.error is None
        assert result.value == "host\n"
        assert seen == [("Read", {"file_path": "/etc/hostname", "__repl_cwd": str(tmp_path)})]
    finally:
        await runner.shutdown_all()


@pytest.mark.asyncio
async def test_runner_auto_awaits_nested_helper_results(tmp_path: Path) -> None:
    runner = ReplRunner()
    seen: list[tuple[str, dict[str, object]]] = []
    try:
        async def run_tool(name: str, input: dict[str, object]) -> NestedToolResult:
            seen.append((name, input))
            return NestedToolResult(tool_name=name, text=f"{name}:{input.get('file_path')}")

        result = await runner.run(
            session_id="s",
            cwd=tmp_path,
            code='o = [Read("a.txt"), Read("b.txt")]',
            run_tool=run_tool,
            timeout_ms=10_000,
        )
        assert result.error is None
        assert result.value == ["Read:a.txt", "Read:b.txt"]
        assert seen == [
            ("Read", {"file_path": "a.txt", "__repl_cwd": str(tmp_path)}),
            ("Read", {"file_path": "b.txt", "__repl_cwd": str(tmp_path)}),
        ]
    finally:
        await runner.shutdown_all()


@pytest.mark.asyncio
async def test_runner_sanitizes_unpickleable_result(tmp_path: Path) -> None:
    runner = ReplRunner()
    try:
        async def run_tool(name: str, input: dict[str, object]) -> NestedToolResult:
            return NestedToolResult(tool_name=name, text="unused")

        result = await runner.run(
            session_id="s",
            cwd=tmp_path,
            code="o = (x for x in [1])",
            run_tool=run_tool,
            timeout_ms=10_000,
        )
        assert result.error is None
        assert isinstance(result.value, str)
        assert "generator object" in result.value
    finally:
        await runner.shutdown_all()


@pytest.mark.asyncio
async def test_runner_forwards_worker_cwd_after_chdir(tmp_path: Path) -> None:
    runner = ReplRunner()
    subdir = tmp_path / "nested"
    subdir.mkdir()
    seen: list[tuple[str, dict[str, object]]] = []
    try:
        async def run_tool(name: str, input: dict[str, object]) -> NestedToolResult:
            seen.append((name, input))
            return NestedToolResult(tool_name=name, text="ok")

        result = await runner.run(
            session_id="s",
            cwd=tmp_path,
            code='chdir("nested")\no = await Read(file_path="note.txt")',
            run_tool=run_tool,
            timeout_ms=10_000,
        )
        assert result.error is None
        assert result.value == "ok"
        assert seen == [("Read", {"file_path": "note.txt", "__repl_cwd": str(subdir)})]
    finally:
        await runner.shutdown_all()


@pytest.mark.asyncio
async def test_runner_timeout_kills_worker(tmp_path: Path) -> None:
    runner = ReplRunner()
    try:
        async def run_tool(name: str, input: dict[str, object]) -> NestedToolResult:
            return NestedToolResult(tool_name=name, text="unused")

        result = await runner.run(
            session_id="s",
            cwd=tmp_path,
            code="while True:\n    pass" if sys.version_info < (0, 0) else "x = sum(range(10**9))",
            run_tool=run_tool,
            timeout_ms=50,
        )
        assert result.error is not None
        assert result.reset is True

        recovered = await runner.run(
            session_id="s",
            cwd=tmp_path,
            code="o = 7",
            run_tool=run_tool,
            timeout_ms=10_000,
        )
        assert recovered.error is None
        assert recovered.reset is True
        assert recovered.value == 7
    finally:
        await runner.shutdown_all()
