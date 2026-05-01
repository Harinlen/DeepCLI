"""Bash — execute a shell command and capture output.

Domain knowledge lives here, not in ToolAuthorizer:

- ``ALLOWLIST_SAFE_COMMANDS`` — argv first-tokens trusted to auto-allow
  (simple commands only).
- ``_COMPOUND_SAFE_COMMANDS`` / ``_GIT_READ_ONLY`` — stricter read-only
  lists for compound command sub-command classification.
- ``DANGEROUS_PATTERNS`` — regexes that force deny.
- ``_DESTRUCTIVE_WARNINGS`` — informational warnings for destructive
  patterns (displayed in the permission prompt, do not affect decisions).
- ``default_risk`` combines the above to produce a ``PermissionSuggestion``.

ToolAuthorizer's ``BashClassifier`` (LLMJudge) is the fallback for
commands that land in "medium / ask" — it escalates to the LLM for a
semantic safety judgment.  This separation keeps Authorizer tool-agnostic.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tasks.id import generate_task_id
from kernel.tasks.output import TaskOutput
from kernel.tasks.registry import TaskRegistry
from kernel.tasks.types import ShellTaskState, TaskStatus, TaskType
from kernel.tools.context import ToolContext
from kernel.tools.tool import RiskContext, Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)
from kernel.tools.builtin.shell_exec import ShellSpec, run_shell_command

logger = logging.getLogger(__name__)


# Argv first-tokens that are trusted to run without prompting.  Draws on
# daemon-era telemetry — these are the commands LLM calls >95% of the
# time in routine dev flows.
ALLOWLIST_SAFE_COMMANDS: frozenset[str] = frozenset(
    {
        "ls",
        "pwd",
        "cat",
        "head",
        "tail",
        "wc",
        "grep",
        "find",
        "echo",
        "printf",
        "which",
        "type",
        "file",
        "stat",
        "date",
        "uname",
        "hostname",
        "whoami",
        "id",
        "env",
        "git",
        "python",
        "python3",
        "uv",
        "node",
        "npm",
        "pnpm",
        "cargo",
        "go",
        "make",
        "pytest",
        "ruff",
        "mypy",
        "tsc",
        "yarn",
    }
)


# Regex patterns that force a ``deny`` decision regardless of the argv
# first token.  Match full command (as a single string) with
# ``re.search``; any hit is considered dangerous.
DANGEROUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+(-[rRf]+\s+)?[/~]"),  # rm of root/home
    re.compile(r"\bdd\s+.*\bof=/dev/"),  # dd writing to device
    re.compile(r"\bmkfs\."),  # format filesystem
    re.compile(r":\(\)\s*\{.*;:"),  # fork bomb
    re.compile(r"\bchmod\s+-R\s+[0-7]{3,4}\s+[/~]"),  # chmod -R on root/home
    re.compile(r"\bshutdown\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bhalt\b"),
    re.compile(r"\bgit\s+push\s+.*--force\b"),
    re.compile(r"\bgit\s+push\s+.*-f\b(?!orce-with-lease)"),
)


COMPOUND_TOKENS: tuple[str, ...] = ("&&", "||", "|", ";", "$(", "`")
"""Tokens that indicate a compound command; classification gets harder
when present, so we escalate the default decision to ``ask``.

.. note:: ``default_risk`` no longer iterates this tuple directly.  It
   checks sub-shell tokens (``$(`` / backtick) first, then tries
   ``_is_compound_safe`` for simple operators.  The constant is kept for
   backward compatibility (exported in ``__all__``).
"""


# ---------------------------------------------------------------------------
# Compound command read-only classification
# ---------------------------------------------------------------------------
# Stricter than ``ALLOWLIST_SAFE_COMMANDS`` — only commands that *never*
# mutate state, regardless of arguments.  Used exclusively by
# ``_is_compound_safe`` so that ``python -c "rm -rf /" | cat`` is NOT
# auto-allowed.  Ported from daemon ``bash_safety.py:_READ_ONLY_SIMPLE``.

_COMPOUND_SAFE_COMMANDS: frozenset[str] = frozenset(
    {
        # List / search
        "ls",
        "tree",
        "du",
        "find",
        "fd",
        "locate",
        "grep",
        "rg",
        "ag",
        "ack",
        "which",
        "whereis",
        # Read
        "cat",
        "head",
        "tail",
        "less",
        "more",
        "wc",
        "stat",
        "file",
        "strings",
        "jq",
        "yq",
        "awk",
        "cut",
        "sort",
        "uniq",
        "tr",
        "column",
        "diff",
        "comm",
        # Info
        "echo",
        "printf",
        "true",
        "false",
        "pwd",
        "whoami",
        "date",
        "uname",
        "env",
        "printenv",
        "hostname",
        "id",
        "uptime",
        "free",
        "df",
        "lsblk",
        "lscpu",
        # Dev tools (read-only)
        "cloc",
        "sha256sum",
        "md5sum",
        "xxd",
        "hexdump",
        "base64",
        "man",
        "type",
        "command",
    }
)

_GIT_READ_ONLY: frozenset[str] = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "branch",
        "remote",
        "tag",
        "describe",
        "rev-parse",
        "rev-list",
        "ls-files",
        "ls-tree",
        "stash list",
        "shortlog",
        "blame",
    }
)


# ---------------------------------------------------------------------------
# Destructive command warnings (informational — does not affect decisions)
# ---------------------------------------------------------------------------
# Ported from daemon ``bash_safety.py:_DESTRUCTIVE_PATTERNS``.

_DESTRUCTIVE_WARNINGS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bgit\s+reset\s+--hard\b"),
        "may discard uncommitted changes",
    ),
    (
        re.compile(r"\bgit\s+push\b[^;&|\n]*\s+(--force|--force-with-lease|-f)\b"),
        "may overwrite remote history",
    ),
    (
        re.compile(
            r"\bgit\s+clean\b(?![^;&|\n]*(?:-[a-zA-Z]*n|--dry-run))[^;&|\n]*-[a-zA-Z]*f"
        ),
        "may permanently delete untracked files",
    ),
    (
        re.compile(r"\bgit\s+checkout\s+(--\s+)?\."),
        "may discard all working tree changes",
    ),
    (
        re.compile(r"\bgit\s+restore\s+(--\s+)?\."),
        "may discard all working tree changes",
    ),
    (
        re.compile(r"\bgit\s+stash\s+(drop|clear)\b"),
        "may permanently delete stashed changes",
    ),
    (
        re.compile(r"\bgit\s+branch\s+(-D\s|--delete\s+--force|--force\s+--delete)"),
        "may force-delete a branch",
    ),
    (
        re.compile(
            r"(^|[;&|\n]\s*)rm\s+-[a-zA-Z]*[rR][a-zA-Z]*f"
            r"|(^|[;&|\n]\s*)rm\s+-[a-zA-Z]*f[a-zA-Z]*[rR]"
        ),
        "may recursively force-remove files",
    ),
    (
        re.compile(r"(^|[;&|\n]\s*)rm\s+-[a-zA-Z]*[rR]"),
        "may recursively remove files",
    ),
    (
        re.compile(r"\b(DROP|TRUNCATE)\s+(TABLE|DATABASE|SCHEMA)\b", re.IGNORECASE),
        "may drop or truncate database objects",
    ),
    (
        re.compile(r"\bDELETE\s+FROM\s+\w+\s*(;|\"|'|\n|$)", re.IGNORECASE),
        "may delete all rows from a table",
    ),
    (
        re.compile(r"\bkubectl\s+delete\b"),
        "may delete Kubernetes resources",
    ),
    (
        re.compile(r"\bterraform\s+destroy\b"),
        "may destroy infrastructure",
    ),
]


class BashTool(Tool[dict[str, Any], str]):
    """Execute a shell command and capture stdout + stderr."""

    extra_safe_commands: frozenset[str] = frozenset()
    """User-configured extra safe commands (from ``permissions.bash_safe_commands``).

    Set by ``ToolManager.startup()`` after reading the permissions config.
    Participates in both simple-command allowlist and compound-command
    read-only classification.  ``DANGEROUS_PATTERNS`` still override these.
    """

    name = "Bash"
    description_key = "tools/bash"
    description = "Execute a bash command."
    kind = ToolKind.execute
    interrupt_behavior = "cancel"

    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to execute.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": (
                    "Optional timeout in milliseconds (max 600000). "
                    "Default 120000 (2 minutes)."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise description of what this command does in "
                    "active voice. Never use words like \"complex\" or \"risk\" "
                    "in the description - just describe what it does."
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": (
                    "Set to true to run this command in the background. "
                    "Use FileRead to read the output later."
                ),
            },
        },
        "required": ["command"],
    }

    # ------------------------------------------------------------------
    # Information source for ToolAuthorizer
    # ------------------------------------------------------------------

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        command = input.get("command", "")
        if not isinstance(command, str) or not command.strip():
            return PermissionSuggestion(
                risk="medium", default_decision="ask", reason="empty command"
            )

        # Dangerous-pattern match wins over everything — safety first.
        for pattern in DANGEROUS_PATTERNS:
            if pattern.search(command):
                return PermissionSuggestion(
                    risk="high",
                    default_decision="deny",
                    reason=f"matches dangerous pattern {pattern.pattern!r}",
                )

        # Sub-shell expressions cannot be statically analysed — always ask.
        if "$(" in command or "`" in command:
            return PermissionSuggestion(
                risk="medium",
                default_decision="ask",
                reason="sub-shell needs review",
            )

        # Simple compound operators — try sub-command-level classification.
        if any(t in command for t in ("&&", "||", "|", ";")):
            if _is_compound_safe(command, self.extra_safe_commands):
                return PermissionSuggestion(
                    risk="low",
                    default_decision="allow",
                    reason="all sub-commands in read-only list",
                )
            return PermissionSuggestion(
                risk="medium",
                default_decision="ask",
                reason="compound command needs review",
            )

        # Simple (non-compound) command.
        safe = ALLOWLIST_SAFE_COMMANDS | self.extra_safe_commands
        head = _argv_head(command)
        if head is None:
            return PermissionSuggestion(
                risk="medium",
                default_decision="ask",
                reason="could not parse argv",
            )
        if head in safe:
            return PermissionSuggestion(
                risk="low",
                default_decision="allow",
                reason=f"safe allowlist: {head!r}",
            )
        return PermissionSuggestion(
            risk="medium",
            default_decision="ask",
            reason=f"unclassified command: {head!r}",
        )

    def prepare_permission_matcher(self, input: dict[str, Any]):  # noqa: ANN201
        command = str(input.get("command", ""))

        def matcher(pattern: str) -> bool:
            # "git:*" or "npm install:*"  → prefix match on command
            if pattern.endswith(":*"):
                prefix = pattern[:-2].rstrip()
                return command == prefix or command.startswith(prefix + " ")
            # Wildcard match — "*secrets.db*" or "sqlite3*" style patterns.
            if "*" in pattern or "?" in pattern:
                from fnmatch import fnmatch
                return fnmatch(command, pattern)
            # Exact command match — user pre-approved a specific invocation.
            return command == pattern

        return matcher

    def is_destructive(self, input: dict[str, Any]) -> bool:
        command = str(input.get("command", ""))
        return any(p.search(command) for p in DANGEROUS_PATTERNS)

    def destructive_warning(self, input: dict[str, Any]) -> str | None:
        """Return a human-readable warning if command matches a destructive pattern.

        Informational only — does not affect permission decisions.
        Multiple matches are joined with ``"; "``.
        """
        command = str(input.get("command", ""))
        warnings: list[str] = []
        for pattern, message in _DESTRUCTIVE_WARNINGS:
            if pattern.search(command):
                warnings.append(message)
        return "; ".join(warnings) if warnings else None

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def validate_input(self, input: dict[str, Any], ctx: RiskContext) -> None:
        command = input.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolInputError("command must be a non-empty string")
        if len(command) > 32_000:
            raise ToolInputError("command exceeds 32,000 character limit")

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        command = input["command"]
        timeout_ms = int(input.get("timeout_ms") or 120_000)
        run_in_background = bool(input.get("run_in_background", False))
        description = input.get("description") or command[:80]

        if run_in_background and ctx.tasks is not None:
            yield await self._spawn_background(command, description, timeout_ms, ctx)
            return

        shell = shutil.which("bash") or shutil.which("sh")
        if shell is None:
            error = "neither bash nor sh found on PATH"
            yield ToolCallResult(
                data={"exit_code": -1, "stdout": "", "stderr": error},
                llm_content=[TextBlock(type="text", text=error)],
                display=TextDisplay(text=error),
            )
            return

        async for event in run_shell_command(
            ShellSpec(argv=[shell, "-lc", command]),
            cwd=ctx.cwd,
            env=ctx.env,
            timeout_ms=timeout_ms,
        ):
            yield event

    async def _spawn_background(
        self,
        command: str,
        description: str,
        timeout_ms: int,
        ctx: ToolContext,
    ) -> ToolCallResult:
        """Spawn a background shell task, return task_id immediately."""
        task_id = generate_task_id(TaskType.local_bash)
        output = TaskOutput(ctx.session_id, task_id)
        output_path = await output.init_file()

        # Open file fd for subprocess to write directly (zero Python memory)
        fd = os.open(output_path, os.O_WRONLY | os.O_APPEND)

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=fd,
            stderr=fd,
            cwd=str(ctx.cwd),
            env={**os.environ, **ctx.env} if ctx.env else None,
        )
        os.close(fd)  # child inherited the fd

        task = ShellTaskState(
            id=task_id,
            status=TaskStatus.running,
            description=description,
            owner_agent_id=ctx.agent_id,
            command=command,
            output_file=output_path,
            process=process,
        )
        ctx.tasks.register(task)  # type: ignore[union-attr]

        asyncio.create_task(
            _wait_and_notify(task_id, process, timeout_ms, ctx.tasks)  # type: ignore[arg-type]
        )
        asyncio.create_task(
            _stall_watchdog(task_id, description, output_path, ctx.tasks, ctx.queue_reminders)  # type: ignore[arg-type]
        )

        body = (
            f"Command running in background with ID: {task_id}. "
            f"Output is being written to: {output_path}"
        )
        return ToolCallResult(
            data={"task_id": task_id, "status": "running"},
            llm_content=[TextBlock(type="text", text=body)],
            display=TextDisplay(text=body),
        )


# ---------------------------------------------------------------------------
# Background task helpers (module-level, stateless)
# ---------------------------------------------------------------------------


async def _wait_and_notify(
    task_id: str,
    process: asyncio.subprocess.Process,
    timeout_ms: int,
    registry: TaskRegistry,
) -> None:
    """Wait for background process to exit, update registry, push notification."""
    try:
        returncode = await asyncio.wait_for(process.wait(), timeout=timeout_ms / 1000.0)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        returncode = -1

    status = TaskStatus.completed if returncode == 0 else TaskStatus.failed
    registry.update_status(task_id, status, exit_code=returncode)
    registry.enqueue_notification(task_id)


# ---------------------------------------------------------------------------
# Stall watchdog
# ---------------------------------------------------------------------------

STALL_CHECK_INTERVAL_S = 5.0
STALL_THRESHOLD_S = 45.0
STALL_TAIL_BYTES = 1024

PROMPT_PATTERNS = [
    re.compile(r"\(y/n\)", re.IGNORECASE),
    re.compile(r"\[y/n\]", re.IGNORECASE),
    re.compile(r"\(yes/no\)", re.IGNORECASE),
    re.compile(
        r"\b(?:Do you|Would you|Shall I|Are you sure)\b.*\?\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"Press (any key|Enter)", re.IGNORECASE),
    re.compile(r"Continue\?", re.IGNORECASE),
    re.compile(r"Overwrite\?", re.IGNORECASE),
]


def _looks_like_prompt(tail: str) -> bool:
    """Check if the tail of command output looks like an interactive prompt."""
    last_line = tail.rstrip().rsplit("\n", 1)[-1]
    return any(p.search(last_line) for p in PROMPT_PATTERNS)


async def _stall_watchdog(
    task_id: str,
    description: str,
    output_path: str,
    registry: TaskRegistry,
    queue_reminders: Callable[[list[str]], None] | None,
) -> None:
    """Periodically check if a background command is stuck on an interactive prompt.

    Does NOT call ``registry.enqueue_notification()`` (which sets
    ``notified=True`` and would suppress the real completion notification).
    Instead pushes directly to ``queue_reminders``.
    """
    last_size = 0
    last_growth = time.time()

    while True:
        await asyncio.sleep(STALL_CHECK_INTERVAL_S)
        task = registry.get(task_id)
        if task is None or task.status.is_terminal:
            return

        try:
            size = os.path.getsize(output_path)
        except FileNotFoundError:
            continue

        if size > last_size:
            last_size = size
            last_growth = time.time()
            continue

        if time.time() - last_growth < STALL_THRESHOLD_S:
            continue

        # Read tail and check for interactive prompt patterns
        try:
            with open(output_path, "rb") as f:
                f.seek(max(0, size - STALL_TAIL_BYTES))
                tail = f.read().decode("utf-8", errors="replace")
        except FileNotFoundError:
            continue

        if not _looks_like_prompt(tail):
            last_growth = time.time()
            continue

        if queue_reminders is not None:
            notification = (
                f"<task-notification>\n"
                f"<task-id>{task_id}</task-id>\n"
                f'<summary>Background command "{description}" appears to be '
                f"waiting for interactive input</summary>\n"
                f"</task-notification>\n"
                f"Last output:\n{tail.rstrip()}\n\n"
                f"The command is likely blocked on an interactive prompt. "
                f"Kill this task and re-run with piped input (e.g., "
                f"`echo y | command`) or a non-interactive flag if one exists."
            )
            queue_reminders([notification])
        return  # notify once only


def _argv_head(command: str) -> str | None:
    """Return the first argv token, or ``None`` on parse failure."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    return tokens[0] if tokens else None


# ---------------------------------------------------------------------------
# Compound command safety classification
# ---------------------------------------------------------------------------


def _extract_commands(command: str) -> list[str]:
    """Split a compound shell command into individual sub-commands.

    Splits on ``&&``, ``||``, ``;``, and ``|``.  Returns stripped
    fragments; empty fragments are dropped.

    Ported from daemon ``bash_safety.py:_extract_commands``.
    """
    parts = re.split(r"\s*(?:&&|\|\||[;|])\s*", command.strip())
    return [p.strip() for p in parts if p.strip()]


def _base_command(fragment: str) -> tuple[str, str | None]:
    """Extract the base command and optional git sub-command from a fragment.

    Returns ``(head, git_sub)`` where *git_sub* is set when *head* is
    ``"git"`` (e.g. ``("git", "status")``).  For non-git commands
    ``git_sub`` is ``None``.
    """
    tokens = fragment.split()
    if not tokens:
        return "", None
    head = tokens[0]
    if head == "git" and len(tokens) >= 2:
        return head, tokens[1]
    return head, None


def _is_compound_safe(command: str, extra: frozenset[str] = frozenset()) -> bool:
    """Return ``True`` if *command* consists entirely of read-only operations.

    Uses the strict ``_COMPOUND_SAFE_COMMANDS`` list (not
    ``ALLOWLIST_SAFE_COMMANDS``) to prevent auto-allowing executable
    tools (python, npm, etc.) in compound context.

    Args:
        command: Shell command string with compound operators.
        extra: User-configured extra safe commands from
            ``permissions.bash_safe_commands``.
    """
    safe = _COMPOUND_SAFE_COMMANDS | extra
    fragments = _extract_commands(command)
    if not fragments:
        return False

    for frag in fragments:
        head, git_sub = _base_command(frag)
        if not head:
            return False
        if head == "git":
            if git_sub is None:
                return False  # bare "git" with no sub-command
            if git_sub not in _GIT_READ_ONLY:
                return False
            continue
        if head in safe:
            continue
        return False

    return True


__all__ = [
    "ALLOWLIST_SAFE_COMMANDS",
    "BashTool",
    "COMPOUND_TOKENS",
    "DANGEROUS_PATTERNS",
]
