"""Tests for kernel.tasks.output."""

import os

import pytest

from kernel.tasks.output import TaskOutput


@pytest.fixture
def task_output(tmp_path: object) -> TaskOutput:
    """TaskOutput with a session dir under tmp_path."""
    # Patch the output dir to use tmp_path
    out = TaskOutput.__new__(TaskOutput)
    out.session_id = "test-session"
    out.task_id = "b00000001"
    out.path = str(tmp_path) + "/b00000001.output"  # type: ignore[operator]
    return out


class TestInitFile:
    @pytest.mark.asyncio
    async def test_creates_file(self, tmp_path: object) -> None:
        out = TaskOutput.__new__(TaskOutput)
        out.session_id = "s"
        out.task_id = "btest"
        out.path = str(tmp_path) + "/btest.output"  # type: ignore[operator]
        path = await out.init_file()
        assert os.path.exists(path)
        assert os.path.getsize(path) == 0

    @pytest.mark.asyncio
    async def test_permissions(self, tmp_path: object) -> None:
        out = TaskOutput.__new__(TaskOutput)
        out.session_id = "s"
        out.task_id = "bperm"
        out.path = str(tmp_path) + "/bperm.output"  # type: ignore[operator]
        path = await out.init_file()
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600


class TestReadAll:
    @pytest.mark.asyncio
    async def test_empty_file(self, task_output: TaskOutput) -> None:
        with open(task_output.path, "w") as f:
            f.write("")
        result = await task_output.read_all()
        assert result == ""

    @pytest.mark.asyncio
    async def test_small_file(self, task_output: TaskOutput) -> None:
        with open(task_output.path, "w") as f:
            f.write("hello world\n")
        result = await task_output.read_all()
        assert result == "hello world\n"

    @pytest.mark.asyncio
    async def test_missing_file(self, task_output: TaskOutput) -> None:
        result = await task_output.read_all()
        assert result == ""

    @pytest.mark.asyncio
    async def test_max_bytes_cap(self, task_output: TaskOutput) -> None:
        with open(task_output.path, "w") as f:
            f.write("x" * 1000)
        result = await task_output.read_all(max_bytes=100)
        assert len(result) == 100


class TestReadTail:
    @pytest.mark.asyncio
    async def test_small_file_returns_all(self, task_output: TaskOutput) -> None:
        with open(task_output.path, "w") as f:
            f.write("line1\nline2\n")
        result = await task_output.read_tail(max_bytes=1000)
        assert result == "line1\nline2\n"

    @pytest.mark.asyncio
    async def test_large_file_truncates_head(self, task_output: TaskOutput) -> None:
        with open(task_output.path, "w") as f:
            f.write("A" * 500 + "B" * 500)
        result = await task_output.read_tail(max_bytes=500)
        assert "omitted" in result
        assert "B" * 500 in result

    @pytest.mark.asyncio
    async def test_missing_file(self, task_output: TaskOutput) -> None:
        result = await task_output.read_tail()
        assert result == ""


class TestReadDelta:
    @pytest.mark.asyncio
    async def test_incremental(self, task_output: TaskOutput) -> None:
        with open(task_output.path, "w") as f:
            f.write("first\nsecond\n")
        data1, off1 = await task_output.read_delta(0)
        assert "first" in data1
        assert off1 > 0

        # Append more
        with open(task_output.path, "a") as f:
            f.write("third\n")
        data2, off2 = await task_output.read_delta(off1)
        assert "third" in data2
        assert off2 > off1

    @pytest.mark.asyncio
    async def test_missing_file(self, task_output: TaskOutput) -> None:
        data, off = await task_output.read_delta(0)
        assert data == ""
        assert off == 0


class TestCleanup:
    @pytest.mark.asyncio
    async def test_removes_file(self, task_output: TaskOutput) -> None:
        with open(task_output.path, "w") as f:
            f.write("data")
        await task_output.cleanup()
        assert not os.path.exists(task_output.path)

    @pytest.mark.asyncio
    async def test_missing_file_no_error(self, task_output: TaskOutput) -> None:
        await task_output.cleanup()  # should not raise
