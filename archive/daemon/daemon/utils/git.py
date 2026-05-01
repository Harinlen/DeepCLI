"""Git context collection for system prompt injection.

Runs git commands in parallel to collect repository state.  Results
are meant to be cached per-session (snapshot semantics — the LLM
should use ``bash git status`` for real-time state).

Memoize strategy (docs/planning/phase4-batch1.md §4.7 决策 1):
- First query of a session fetches and caches the status
- Subsequent queries reuse the cache (snapshot semantics)
- Session ``resume`` explicitly invalidates the cache (may be stale)
- ``/compact`` does NOT invalidate (protects future prompt cache)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_STATUS_CHARS = 2000
"""Truncate ``git status --short`` output beyond this many characters."""

_CMD_TIMEOUT = 5.0
"""Per-command timeout in seconds."""

_TRUNCATION_SUFFIX = "\n... (truncated. Use Bash git status for full output)"


async def _run_git(args: list[str], cwd: Path) -> str | None:
    """Run a single git command, returning stripped stdout or ``None``.

    Any failure — non-zero exit, timeout, ``git`` not installed, I/O
    error — results in ``None``.  Callers treat ``None`` as "command
    failed, skip this field".

    Args:
        args: Arguments passed to ``git`` (e.g. ``["rev-parse", "HEAD"]``).
        cwd: Working directory for the subprocess.

    Returns:
        Stripped stdout on success, ``None`` on any failure.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_CMD_TIMEOUT)
        except asyncio.TimeoutError:
            # Kill the lingering process to avoid leaks.
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return None
        if proc.returncode != 0:
            return None
        return stdout.decode("utf-8", errors="replace").strip()
    except (FileNotFoundError, OSError):
        return None


async def _detect_default_branch(cwd: Path) -> str | None:
    """Detect the default branch name.

    Tries ``main`` first, then ``master``.  Returns ``None`` if
    neither exists (repo may use a custom default).
    """
    for name in ("main", "master"):
        result = await _run_git(["rev-parse", "--verify", name], cwd)
        if result is not None:
            return name
    return None


def _format_status_block(status: str | None) -> str:
    """Format the working-tree status section with truncation."""
    if not status:
        return "(clean)"
    if len(status) > MAX_STATUS_CHARS:
        return status[:MAX_STATUS_CHARS] + _TRUNCATION_SUFFIX
    return status


def _format_git_status(
    branch: str | None,
    default_branch: str | None,
    status: str | None,
    log: str | None,
    user: str | None,
) -> str | None:
    """Assemble the git context text block for the system prompt.

    If all core fields are missing, returns ``None`` (caller should
    omit the section entirely).

    Args:
        branch: Current branch name.
        default_branch: Default/main branch name (``main`` or ``master``).
        status: ``git status --short`` output.
        log: ``git log --oneline -n 5`` output.
        user: ``git config user.name`` output.

    Returns:
        Formatted multi-line text, or ``None`` if nothing to show.
    """
    # If nothing meaningful came back, bail out.
    if branch is None and status is None and log is None:
        return None

    lines: list[str] = [
        (
            "This is the git status at the start of the conversation. "
            "Note that this status is a snapshot in time, and will not "
            "update during the conversation."
        ),
    ]

    if branch:
        lines.append("")
        lines.append(f"Current branch: {branch}")
    if default_branch:
        lines.append("")
        lines.append(f"Main branch (you will usually use this for PRs): {default_branch}")
    if user:
        lines.append("")
        lines.append(f"Git user: {user}")

    lines.append("")
    lines.append("Status:")
    lines.append(_format_status_block(status))

    if log:
        lines.append("")
        lines.append("Recent commits:")
        lines.append(log)

    return "\n".join(lines)


async def get_git_status(cwd: Path) -> str | None:
    """Collect git context for a working directory.

    Runs five git commands in parallel:

    1. ``git rev-parse --is-inside-work-tree`` — gate check
    2. ``git rev-parse --abbrev-ref HEAD`` — current branch
    3. ``git rev-parse --verify main|master`` — default branch
    4. ``git --no-optional-locks status --short`` — working tree
    5. ``git --no-optional-locks log --oneline -n 5`` — recent commits
    6. ``git config user.name`` — user name

    All commands use ``--no-optional-locks`` where applicable to
    avoid acquiring the object-database lock during reads.

    Args:
        cwd: Working directory (must be inside a git repo).

    Returns:
        Formatted text block for the system prompt, or ``None`` if
        the directory is not inside a git repo, ``git`` is unavailable,
        or every subcommand fails.
    """
    is_inside = await _run_git(["rev-parse", "--is-inside-work-tree"], cwd)
    if is_inside is None:
        return None

    branch, default_branch, status, log, user = await asyncio.gather(
        _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd),
        _detect_default_branch(cwd),
        _run_git(["--no-optional-locks", "status", "--short"], cwd),
        _run_git(["--no-optional-locks", "log", "--oneline", "-n", "5"], cwd),
        _run_git(["config", "user.name"], cwd),
    )

    return _format_git_status(branch, default_branch, status, log, user)
