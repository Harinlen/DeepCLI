"""File-based tool result spillover helpers for session storage."""

from __future__ import annotations

import secrets
from pathlib import Path


def aux_dir(sessions_dir: Path, session_id: str) -> Path:
    """Return the per-session auxiliary directory."""
    return sessions_dir / session_id


def tool_results_dir(sessions_dir: Path, session_id: str) -> Path:
    """Return the per-session tool results directory."""
    return aux_dir(sessions_dir, session_id) / "tool-results"


def write_spilled(sessions_dir: Path, session_id: str, content: str) -> tuple[str, str]:
    """Write oversized tool output to a sidecar file."""
    result_hash = secrets.token_hex(8)
    tr_dir = tool_results_dir(sessions_dir, session_id)
    tr_dir.mkdir(parents=True, exist_ok=True)
    (tr_dir / f"{result_hash}.txt").write_text(content, encoding="utf-8")
    return f"{session_id}/tool-results/{result_hash}.txt", result_hash


def read_spilled(sessions_dir: Path, session_id: str, result_hash: str) -> str:
    """Read a previously spilled tool output sidecar."""
    path = tool_results_dir(sessions_dir, session_id) / f"{result_hash}.txt"
    return path.read_text(encoding="utf-8")
