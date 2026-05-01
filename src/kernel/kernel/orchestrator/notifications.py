"""Task notification formatting for Orchestrator reminders."""

from __future__ import annotations


def format_task_notification(task: object) -> str:
    """Format a completed task as a ``<task-notification>`` XML block.

    Args:
        task: Task state object from TaskRegistry.

    Returns:
        XML-ish reminder text queued for the next model turn.
    """
    from kernel.tasks.types import AgentTaskState, MonitorTaskState, ShellTaskState

    raw_status = getattr(task, "status", "unknown")
    status = getattr(raw_status, "value", str(raw_status))
    description = str(getattr(task, "description", ""))
    task_id = str(getattr(task, "id", ""))

    if isinstance(task, MonitorTaskState):
        summary = _monitor_summary(task, description, status)
    elif isinstance(task, ShellTaskState):
        summary = _shell_summary(task, description, status)
    elif isinstance(task, AgentTaskState):
        summary = _agent_summary(task, description, status)
    else:
        summary = f'Task "{description}" {status}'

    tool_use_id = getattr(task, "tool_use_id", None)
    tool_use_line = f"\n<tool-use-id>{tool_use_id}</tool-use-id>" if tool_use_id else ""
    result = getattr(task, "result", None)
    result_section = (
        f"\n<result>{result}</result>" if isinstance(task, AgentTaskState) and result else ""
    )
    output_file = getattr(task, "output_file", "")
    return (
        f"<task-notification>\n"
        f"<task-id>{task_id}</task-id>{tool_use_line}\n"
        f"<output-file>{output_file}</output-file>\n"
        f"<status>{status}</status>\n"
        f"<summary>{summary}</summary>{result_section}\n"
        f"</task-notification>"
    )


def format_monitor_notification(task_id: str, lines: list[str]) -> str:
    """Format buffered monitor lines as a ``<monitor-update>`` XML block.

    Args:
        task_id: Monitor task id that produced the output.
        lines: Buffered output lines since the last drain.

    Returns:
        XML-ish reminder text containing monitor output.
    """
    body = "\n".join(lines)
    return (
        f"<monitor-update>\n"
        f"<task-id>{task_id}</task-id>\n"
        f"<output>\n{body}\n</output>\n"
        f"</monitor-update>"
    )


def _monitor_summary(task: object, description: str, status: str) -> str:
    """Build a concise summary for monitor task completion.

    Args:
        task: Monitor task state or compatible object.
        description: Human-facing task description.
        status: Normalized task status string.

    Returns:
        One-line summary for the notification body.
    """
    exit_code = getattr(task, "exit_code", None)
    if status == "completed":
        summary = f'Monitor "{description}" stopped'
        if exit_code is not None:
            summary += f" (exit code {exit_code})"
    elif status == "failed":
        summary = f'Monitor "{description}" failed'
        if exit_code is not None:
            summary += f" with exit code {exit_code}"
    else:
        summary = f'Monitor "{description}" was stopped'
    return summary


def _shell_summary(task: object, description: str, status: str) -> str:
    """Build a concise summary for background shell completion.

    Args:
        task: Shell task state or compatible object.
        description: Human-facing command description.
        status: Normalized task status string.

    Returns:
        One-line summary for the notification body.
    """
    exit_code = getattr(task, "exit_code", None)
    if status == "completed":
        summary = f'Background command "{description}" completed'
        if exit_code is not None:
            summary += f" (exit code {exit_code})"
    elif status == "failed":
        summary = f'Background command "{description}" failed'
        if exit_code is not None:
            summary += f" with exit code {exit_code}"
    else:
        summary = f'Background command "{description}" was stopped'
    return summary


def _agent_summary(task: object, description: str, status: str) -> str:
    """Build a concise summary for background agent completion.

    Args:
        task: Agent task state or compatible object.
        description: Human-facing agent task description.
        status: Normalized task status string.

    Returns:
        One-line summary for the notification body.
    """
    if status == "completed":
        return f'Agent "{description}" completed'
    if status == "failed":
        error = getattr(task, "error", None) or "Unknown error"
        return f'Agent "{description}" failed: {error}'
    return f'Agent "{description}" was stopped'
