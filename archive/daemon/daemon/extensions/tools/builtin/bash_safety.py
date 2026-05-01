"""Bash command safety analysis — read-only classification and destructive warnings.

Two complementary features:

1. **Read-only classification**: determines whether a command is safe
   to auto-approve (``PermissionLevel.NONE``) by checking against a
   known allowlist of read-only commands.

2. **Destructive warnings**: detects dangerous patterns (``rm -rf``,
   ``git push --force``, etc.) and returns a human-readable warning
   displayed in the permission prompt.  Informational only — does not
   block execution.

Aligned with Claude Code's ``readOnlyValidation.ts`` and
``destructiveCommandWarning.ts``.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Read-only command classification
# ---------------------------------------------------------------------------

# Commands that never mutate state — safe to auto-approve.
_READ_ONLY_SIMPLE: frozenset[str] = frozenset(
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
        "wc",
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

# Git sub-commands that are read-only.
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
        "config --list",
        "config --get",
    }
)


def _extract_commands(command: str) -> list[str]:
    """Split a compound shell command into individual sub-commands.

    Splits on ``&&``, ``||``, ``;``, and ``|``.  Returns the
    first-word (or ``git <sub>``) of each fragment.
    """
    # Split on shell operators — captures the delimiter but we discard it.
    parts = re.split(r"\s*(?:&&|\|\||[;|])\s*", command.strip())
    return [p.strip() for p in parts if p.strip()]


def _base_command(fragment: str) -> str:
    """Extract the base command from a shell fragment.

    For git, returns ``git <subcommand>`` (e.g. ``git status``).
    For others, returns the first token (e.g. ``ls``).
    """
    tokens = fragment.split()
    if not tokens:
        return ""
    cmd = tokens[0]
    if cmd == "git" and len(tokens) >= 2:
        return f"git {tokens[1]}"
    return cmd


def is_read_only_command(command: str) -> bool:
    """Return ``True`` if *command* consists entirely of read-only operations.

    Compound commands (pipes, ``&&``, etc.) are safe only if *every*
    sub-command is read-only.

    Args:
        command: Shell command string.
    """
    fragments = _extract_commands(command)
    if not fragments:
        return False

    for frag in fragments:
        base = _base_command(frag)
        if not base:
            return False
        # Check simple commands.
        if base in _READ_ONLY_SIMPLE:
            continue
        # Check git read-only sub-commands.
        if base.startswith("git "):
            git_sub = base[4:]  # e.g. "status", "log"
            if git_sub in _GIT_READ_ONLY:
                continue
            return False
        # Unknown command → not read-only.
        return False

    return True


# ---------------------------------------------------------------------------
# Destructive command warnings
# ---------------------------------------------------------------------------

_DESTRUCTIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Git — data loss
    (
        re.compile(r"\bgit\s+reset\s+--hard\b"),
        "may discard uncommitted changes",
    ),
    (
        re.compile(r"\bgit\s+push\b[^;&|\n]*\s+(--force|--force-with-lease|-f)\b"),
        "may overwrite remote history",
    ),
    (
        re.compile(r"\bgit\s+clean\b(?![^;&|\n]*(?:-[a-zA-Z]*n|--dry-run))[^;&|\n]*-[a-zA-Z]*f"),
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
    # File deletion
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
    # Database
    (
        re.compile(r"\b(DROP|TRUNCATE)\s+(TABLE|DATABASE|SCHEMA)\b", re.IGNORECASE),
        "may drop or truncate database objects",
    ),
    (
        re.compile(r"\bDELETE\s+FROM\s+\w+\s*(;|\"|'|\n|$)", re.IGNORECASE),
        "may delete all rows from a table",
    ),
    # Infrastructure
    (
        re.compile(r"\bkubectl\s+delete\b"),
        "may delete Kubernetes resources",
    ),
    (
        re.compile(r"\bterraform\s+destroy\b"),
        "may destroy infrastructure",
    ),
]


def get_destructive_warning(command: str) -> str | None:
    """Return a human-readable warning if *command* matches a dangerous pattern.

    Informational only — does not affect permission decisions.
    Multiple matches are joined with ``"; "``.

    Args:
        command: Shell command string.

    Returns:
        Warning string or ``None`` if no dangerous pattern matched.
    """
    warnings: list[str] = []
    for pattern, message in _DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            warnings.append(message)
    return "; ".join(warnings) if warnings else None
