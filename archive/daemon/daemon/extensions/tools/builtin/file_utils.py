"""Shared file validation helpers for file-based tools.

Extracted from file_read, file_write, and file_edit to eliminate
code duplication.
"""

from __future__ import annotations

from pathlib import Path

from daemon.extensions.tools.base import ToolResult

# Heuristic: if the first 8 KB contain a null byte, treat as binary
_BINARY_CHECK_BYTES = 8192


def validate_absolute_path(file_path: str) -> ToolResult | None:
    """Return an error ToolResult if *file_path* is not absolute, else None."""
    if not Path(file_path).is_absolute():
        return ToolResult(
            output=f"file_path must be absolute, got: {file_path}",
            is_error=True,
        )
    return None


def validate_file_exists(file_path: str) -> ToolResult | None:
    """Return an error ToolResult if *file_path* doesn't exist or isn't a file."""
    path = Path(file_path)
    if not path.exists():
        return ToolResult(
            output=f"File not found: {file_path}",
            is_error=True,
        )
    if not path.is_file():
        return ToolResult(
            output=f"Not a file (is a directory?): {file_path}",
            is_error=True,
        )
    return None


def check_binary(path: Path) -> ToolResult | None:
    """Return an error ToolResult if *path* looks like a binary file.

    Reads the first 8 KB and checks for null bytes.
    Returns None if the file is text.
    """
    try:
        raw = path.read_bytes()[:_BINARY_CHECK_BYTES]
    except OSError as exc:
        return ToolResult(output=f"Cannot read file: {exc}", is_error=True)

    if b"\x00" in raw:
        return ToolResult(
            output=f"Binary file detected: {path}",
            is_error=True,
        )
    return None
