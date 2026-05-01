"""Session-scoped persistence for the LLM's task list.

The LLM calls :class:`~daemon.extensions.tools.builtin.todo_write.TodoWriteTool`
with the **full** task list each turn (V1 style: overwrite, no
patching).  That list is persisted to
``{session_dir}/{session_id}.tasks.json`` so it survives daemon
restarts and is available for session resume.

Storage format is a single JSON object rather than one file per
task — the list is always written as a whole, and concurrent
writers do not exist (the daemon is the sole writer per session).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

TaskStatus = Literal["pending", "in_progress", "completed"]


class TaskItem(BaseModel):
    """A single task entry.

    Attributes:
        content: Imperative description (``"Run tests"``).
        status: ``pending`` / ``in_progress`` / ``completed``.
        active_form: Present-continuous version (``"Running tests"``),
            used by CLI spinners when a task is ``in_progress``.
    """

    content: str = Field(min_length=1)
    status: TaskStatus
    active_form: str = Field(min_length=1)


class _TaskFilePayload(BaseModel):
    """On-disk envelope for the task list."""

    tasks: list[TaskItem]
    updated_at: str


class TaskStore:
    """Load / save the task list for one session.

    Args:
        session_dir: Directory holding session artefacts.
        session_id: Session identifier — used as the file name prefix.
    """

    def __init__(self, session_dir: Path, session_id: str) -> None:
        self._path = session_dir / f"{session_id}.tasks.json"

    @property
    def path(self) -> Path:
        """Full path to the backing JSON file."""
        return self._path

    def load(self) -> list[TaskItem]:
        """Return the currently stored tasks.

        Missing or malformed files produce an empty list — the tool
        always overwrites the full list anyway, so any corruption
        heals on the next ``save``.
        """
        if not self._path.exists():
            return []

        try:
            raw = self._path.read_text(encoding="utf-8")
            payload = _TaskFilePayload.model_validate_json(raw)
        except (OSError, ValueError) as exc:
            logger.warning("Cannot read tasks file %s: %s", self._path, exc)
            return []

        return list(payload.tasks)

    def save(self, tasks: list[TaskItem]) -> None:
        """Overwrite the task file with the given list."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = _TaskFilePayload(
            tasks=tasks,
            updated_at=datetime.now(tz=timezone.utc).isoformat(),
        )
        self._path.write_text(
            payload.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

    def clear(self) -> None:
        """Remove the tasks file (idempotent)."""
        if self._path.exists():
            self._path.unlink()
