"""TaskRegistry — session-level background task registry.

Design reference: ``docs/plans/task-manager.md`` § 1.3, 1.7.
Claude Code equivalent: ``AppState.tasks`` dict +
``src/utils/task/framework.ts``.

One instance per session.  Pure in-memory — task state does not
outlive the session and is never persisted to disk.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Callable

from kernel.tasks.types import (
    AgentTaskState,
    MonitorTaskState,
    ShellTaskState,
    TaskState,
    TaskStatus,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class TaskRegistry:
    """Session-level background task registry.

    Shared by all Orchestrators in a session tree (root + sub-agents)
    via ``OrchestratorDeps.task_registry``.

    Thread-safety: Python asyncio is cooperative — all methods are
    synchronous dict operations with no ``await`` in critical sections,
    so no locking is needed.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}
        self._listeners: list[Callable[[], None]] = []
        # (task_id, owner_agent_id) — agent_id=None means root agent
        self._notification_queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()
        # TodoWrite data: key = agent_id (None = root agent)
        self._todos: dict[str | None, list[dict[str, str]]] = {}
        # Agent name → task_id mapping for SendMessage addressing.
        self._name_to_id: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Register / update / query
    # ------------------------------------------------------------------

    def register(self, task: TaskState) -> None:
        """Register a new task.

        ``task.owner_agent_id`` should be pre-set by the caller (from
        ``ToolContext.agent_id``).
        """
        task.start_time = time.time()
        self._tasks[task.id] = task
        self._notify_listeners()

    def get(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)

    def get_all(self) -> list[TaskState]:
        return list(self._tasks.values())

    def get_running(self) -> list[TaskState]:
        return [t for t in self._tasks.values() if t.status == TaskStatus.running]

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        exit_code: int | None = None,
        result: str | None = None,
        error: str | None = None,
    ) -> TaskState | None:
        """Update task status.  Terminal statuses auto-set ``end_time``."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.status = status
        if status.is_terminal:
            task.end_time = time.time()
        if isinstance(task, (ShellTaskState, MonitorTaskState)) and exit_code is not None:
            task.exit_code = exit_code
        if isinstance(task, AgentTaskState):
            if result is not None:
                task.result = result
            if error is not None:
                task.error = error
        self._notify_listeners()
        return task

    # ------------------------------------------------------------------
    # Agent name registry
    # ------------------------------------------------------------------

    def register_name(self, name: str, task_id: str) -> bool:
        """Map *name* to *task_id*.  Returns ``False`` if *name* is already taken."""
        if name in self._name_to_id:
            return False
        self._name_to_id[name] = task_id
        return True

    def resolve_name(self, name: str) -> str | None:
        """Look up *name* → task_id.  Returns ``None`` if not found."""
        return self._name_to_id.get(name)

    def unregister_name(self, name: str) -> None:
        """Remove a name mapping (idempotent)."""
        self._name_to_id.pop(name, None)

    def update_name(self, name: str, new_task_id: str) -> None:
        """Point an existing name at a new task_id (for agent resume)."""
        self._name_to_id[name] = new_task_id

    # ------------------------------------------------------------------
    # Pending message queue (SendMessage → sub-agent)
    # ------------------------------------------------------------------

    def queue_message(self, task_id: str, msg: str) -> bool:
        """Append *msg* to the agent's pending queue.

        Returns ``False`` if *task_id* is not an ``AgentTaskState``.
        """
        task = self._tasks.get(task_id)
        if not isinstance(task, AgentTaskState):
            return False
        task.pending_messages.append(msg)
        return True

    def drain_messages(self, task_id: str) -> list[str]:
        """Pop all pending messages for *task_id*.

        Called by the sub-agent's Orchestrator at STEP 0 of each
        query loop iteration.  Returns ``[]`` if none.
        """
        task = self._tasks.get(task_id)
        if not isinstance(task, AgentTaskState) or not task.pending_messages:
            return []
        drained = task.pending_messages[:]
        task.pending_messages.clear()
        return drained

    # ------------------------------------------------------------------
    # Notification pipeline
    # ------------------------------------------------------------------

    def enqueue_notification(self, task_id: str) -> None:
        """Push *task_id* into the notification queue.

        Sets ``notified=True`` on the task so it won't be enqueued
        again.  The Orchestrator drains this at step 6d.
        """
        task = self._tasks.get(task_id)
        if task is None or task.notified:
            return
        task.notified = True
        self._notification_queue.put_nowait((task_id, task.owner_agent_id))

    def drain_notifications(self, *, agent_id: str | None = None) -> list[str]:
        """Non-blocking drain of notifications owned by *agent_id*.

        ``agent_id=None`` targets the root agent.  Non-matching
        notifications are re-queued so the owning Orchestrator can
        drain them later.

        Aligned with Claude Code ``query.ts:1570`` — each agent only
        drains its own notifications.
        """
        matched: list[str] = []
        requeue: list[tuple[str, str | None]] = []
        while not self._notification_queue.empty():
            try:
                item = self._notification_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            tid, owner = item
            if owner == agent_id:
                matched.append(tid)
            else:
                requeue.append(item)
        for item in requeue:
            self._notification_queue.put_nowait(item)
        return matched

    # ------------------------------------------------------------------
    # Monitor drain
    # ------------------------------------------------------------------

    def drain_monitor_lines(self, *, agent_id: str | None = None) -> dict[str, list[str]]:
        """Drain buffered monitor lines for active monitors owned by *agent_id*.

        Returns ``{task_id: [lines...]}`` and clears each task's
        ``recent_lines`` buffer.  Called by the Orchestrator at each
        turn boundary (step 6d') to inject ``<monitor-update>``
        system reminders.
        """
        result: dict[str, list[str]] = {}
        for task in self._tasks.values():
            if (
                isinstance(task, MonitorTaskState)
                and task.owner_agent_id == agent_id
                and task.recent_lines
            ):
                result[task.id] = task.recent_lines[:]
                task.recent_lines.clear()
        return result

    # ------------------------------------------------------------------
    # GC
    # ------------------------------------------------------------------

    def evict_terminal(self) -> list[str]:
        """Remove all notified terminal tasks.  Returns evicted IDs.

        Also cleans up ``_name_to_id`` entries pointing at evicted tasks.
        """
        evicted: list[str] = []
        for task_id, task in list(self._tasks.items()):
            if task.status.is_terminal and task.notified:
                del self._tasks[task_id]
                evicted.append(task_id)
        if evicted:
            # Clean up name mappings that point to evicted tasks.
            evicted_set = set(evicted)
            stale_names = [name for name, tid in self._name_to_id.items() if tid in evicted_set]
            for name in stale_names:
                del self._name_to_id[name]
            self._notify_listeners()
        return evicted

    # ------------------------------------------------------------------
    # Session shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Kill all running tasks and clean up output files.

        Called by SessionManager on session teardown.
        """
        for task in list(self._tasks.values()):
            if task.status != TaskStatus.running:
                continue
            if isinstance(task, (ShellTaskState, MonitorTaskState)) and task.process is not None:
                try:
                    task.process.kill()
                    await task.process.wait()
                except ProcessLookupError:
                    pass
                task.process = None
            if isinstance(task, AgentTaskState) and task.cancel_event is not None:
                task.cancel_event.set()
            task.status = TaskStatus.killed
            task.end_time = time.time()

        for task in self._tasks.values():
            if task.output_file:
                try:
                    os.unlink(task.output_file)
                except FileNotFoundError:
                    pass

        self._tasks.clear()
        logger.debug("TaskRegistry shutdown complete")

    # ------------------------------------------------------------------
    # TodoWrite storage
    # ------------------------------------------------------------------

    def get_todos(self, agent_id: str | None) -> list[dict[str, str]]:
        """Get the todo list for *agent_id* (``None`` = root agent)."""
        return list(self._todos.get(agent_id, []))

    def set_todos(self, agent_id: str | None, todos: list[dict[str, str]]) -> None:
        """Replace the todo list for *agent_id*."""
        if todos:
            self._todos[agent_id] = todos
        else:
            self._todos.pop(agent_id, None)

    # ------------------------------------------------------------------
    # Observers
    # ------------------------------------------------------------------

    def on_change(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a change listener.  Returns an unsubscribe callable."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _notify_listeners(self) -> None:
        for fn in self._listeners:
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass


__all__ = ["TaskRegistry"]
