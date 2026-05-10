"""Primitive tools available through scriptable REPL nested dispatch."""

from __future__ import annotations

REPL_PRIMITIVE_TOOLS: frozenset[str] = frozenset(
    {
        "Bash",
        "Cmd",
        "PowerShell",
        "Python",
        "Read",
        "FileRead",
        "Edit",
        "FileEdit",
        "Write",
        "FileWrite",
        "Glob",
        "Grep",
        "Agent",
    }
)

__all__ = ["REPL_PRIMITIVE_TOOLS"]
