"""Tests for kernel.tasks.id."""

from kernel.tasks.id import generate_task_id
from kernel.tasks.types import TaskType


class TestGenerateTaskId:
    def test_bash_prefix(self) -> None:
        tid = generate_task_id(TaskType.local_bash)
        assert tid[0] == "b"

    def test_agent_prefix(self) -> None:
        tid = generate_task_id(TaskType.local_agent)
        assert tid[0] == "a"

    def test_length(self) -> None:
        tid = generate_task_id(TaskType.local_bash)
        assert len(tid) == 9  # 1 prefix + 8 random

    def test_charset(self) -> None:
        allowed = set("0123456789abcdefghijklmnopqrstuvwxyz")
        for _ in range(100):
            tid = generate_task_id(TaskType.local_bash)
            # suffix chars (skip prefix)
            for ch in tid[1:]:
                assert ch in allowed, f"unexpected char {ch!r} in {tid}"

    def test_uniqueness(self) -> None:
        ids = {generate_task_id(TaskType.local_bash) for _ in range(1000)}
        assert len(ids) == 1000
