"""Memory file I/O layer.

Handles reading/writing markdown files with YAML frontmatter, atomic
writes (temp → ``os.replace``), directory tree management, audit
logging, and prompt-injection scanning.
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .types import (
    CATEGORIES,
    MemoryCategory,
    MemoryEntry,
    MemoryHeader,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FILENAME_RE = re.compile(r"^[a-z0-9_-]+$")
"""Allowed characters for memory file names (D17 sanitize rule)."""

_INDEX_FILE = "index.md"
_LOG_FILE = "log.md"
_LOG_ARCHIVE = "log.archive.md"
_LOG_MAX_LINES = 200
_HISTORY_FILE = "history.md"

# Injection patterns to reject (from Hermes _scan_memory_content)
_INJECTION_PATTERNS = [
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"<\|im_end\|>", re.IGNORECASE),
    re.compile(r"^system\s*:", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^(user|assistant|human)\s*:", re.MULTILINE | re.IGNORECASE),
    re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]{3,}"),  # invisible unicode
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sanitize_filename(name: str) -> str:
    """Validate and return a safe filename stem.

    Raises ``ValueError`` if the name contains disallowed characters
    or attempts directory traversal.
    """
    if not name or ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"Invalid memory name: {name!r}")
    stem = name.removesuffix(".md")
    if not _FILENAME_RE.match(stem):
        raise ValueError(f"Memory name must match [a-z0-9_-]: {name!r}")
    return stem


def scan_content(content: str) -> bool:
    """Return True if content looks safe (no injection patterns).

    Returns False if a prompt-injection pattern is detected.
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(content):
            return False
    return True


def ensure_directory_tree(root: Path) -> None:
    """Create the memory directory tree if it doesn't exist.

    Structure::

        root/
        ├── index.md
        ├── log.md
        ├── profile/
        ├── semantic/
        ├── episodic/
        └── procedural/
    """
    root.mkdir(parents=True, exist_ok=True)
    for cat in CATEGORIES:
        (root / cat).mkdir(exist_ok=True)
    # Touch index and log if missing
    index = root / _INDEX_FILE
    if not index.exists():
        index.write_text("", encoding="utf-8")
    log = root / _LOG_FILE
    if not log.exists():
        log.write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into YAML frontmatter dict and body text."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 4 :].strip()
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, body


def _serialize_frontmatter(header: MemoryHeader, content: str) -> str:
    """Serialize a MemoryHeader + content body into MD with YAML frontmatter."""
    fm: dict[str, Any] = {
        "name": header.name,
        "description": header.description,
        "category": header.category,
        "source": header.source,
        "created": header.created.isoformat(),
        "updated": header.updated.isoformat(),
        "access_count": header.access_count,
        "locked": header.locked,
    }
    fm_text = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
    return f"---\n{fm_text}\n---\n\n{content}\n"


def _parse_datetime(value: Any) -> datetime:
    """Best-effort parse of a datetime value from frontmatter."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def read_memory(path: Path) -> MemoryEntry:
    """Read a single memory file and return a MemoryEntry."""
    text = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)

    # Derive filename stem and relative path components
    stem = path.stem
    category = fm.get("category", "semantic")
    if category not in CATEGORIES:
        category = "semantic"

    source = fm.get("source", "agent")
    if source not in ("user", "agent", "extracted"):
        source = "agent"

    header = MemoryHeader(
        filename=stem,
        name=fm.get("name", stem),
        description=fm.get("description", ""),
        category=category,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        created=_parse_datetime(fm.get("created")),
        updated=_parse_datetime(fm.get("updated")),
        access_count=int(fm.get("access_count", 0)),
        locked=bool(fm.get("locked", False)),
        rel_path=f"{category}/{stem}.md",
    )
    return MemoryEntry(header=header, content=body)


def scan_headers(root: Path) -> list[MemoryHeader]:
    """Scan all .md files in the memory tree and return their headers.

    Skips index.md, log.md, log.archive.md, config.md, history.md.
    """
    skip = {_INDEX_FILE, _LOG_FILE, _LOG_ARCHIVE, "config.md", _HISTORY_FILE}
    headers: list[MemoryHeader] = []

    for cat in CATEGORIES:
        cat_dir = root / cat
        if not cat_dir.is_dir():
            continue
        for md_file in sorted(cat_dir.glob("*.md")):
            if md_file.name in skip:
                continue
            try:
                entry = read_memory(md_file)
                headers.append(entry.header)
            except Exception:
                logger.warning("Failed to parse memory file: %s", md_file)
    return headers


# ---------------------------------------------------------------------------
# Write (atomic)
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically using temp file + os.replace.

    Uses ``fcntl.flock`` to serialize concurrent writers.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    tmp_path = None
    try:
        fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
            suffix=".tmp",
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        os.replace(tmp_path, str(path))
        tmp_path = None  # replaced successfully
    finally:
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def write_memory(
    root: Path,
    category: MemoryCategory,
    header: MemoryHeader,
    content: str,
) -> Path:
    """Write a memory file atomically. Returns the written path."""
    stem = sanitize_filename(header.filename)
    path = root / category / f"{stem}.md"
    text = _serialize_frontmatter(header, content)
    _atomic_write(path, text)
    return path


def append_memory(root: Path, category: MemoryCategory, filename: str, content: str) -> Path:
    """Append content to an existing memory file."""
    stem = sanitize_filename(filename)
    path = root / category / f"{stem}.md"
    if not path.exists():
        raise FileNotFoundError(f"Memory file not found: {path}")
    existing = path.read_text(encoding="utf-8")
    new_text = existing.rstrip() + "\n\n" + content.strip() + "\n"
    _atomic_write(path, new_text)
    return path


def delete_memory(root: Path, category: MemoryCategory, filename: str) -> None:
    """Delete a memory file."""
    stem = sanitize_filename(filename)
    path = root / category / f"{stem}.md"
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Index generation
# ---------------------------------------------------------------------------


def build_index_text(headers: list[MemoryHeader]) -> str:
    """Build index.md content grouped by category.

    Each entry shows the first sentence of the description as a
    navigation pointer (full description is in the file frontmatter).
    """
    by_cat: dict[str, list[MemoryHeader]] = {}
    for h in headers:
        by_cat.setdefault(h.category, []).append(h)

    lines: list[str] = []
    for cat in CATEGORIES:
        cat_headers = by_cat.get(cat, [])
        if not cat_headers:
            continue
        lines.append(f"## {cat}")
        for h in cat_headers:
            # First sentence of description for index
            first_line = h.description.split("\n")[0][:120].strip()
            lines.append(f"- [{h.name}]({h.rel_path}) — {first_line}")
        lines.append("")

    text = "\n".join(lines).strip()
    # Enforce 200 line limit
    text_lines = text.split("\n")
    if len(text_lines) > _LOG_MAX_LINES:
        text = "\n".join(text_lines[:_LOG_MAX_LINES])
        text += "\n\n⚠ Index truncated at 200 lines."
    return text


def write_index(root: Path, headers: list[MemoryHeader]) -> None:
    """Rebuild and write index.md."""
    text = build_index_text(headers)
    _atomic_write(root / _INDEX_FILE, text)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def write_log(root: Path, action: str, name: str, detail: str = "") -> None:
    """Append an audit line to log.md. Rolls to archive at 200 lines."""
    log_path = root / _LOG_FILE
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"- {ts} {action} `{name}`"
    if detail:
        line += f" — {detail}"
    line += "\n"

    # Read existing, append, check rollover
    existing = ""
    if log_path.exists():
        existing = log_path.read_text(encoding="utf-8")
    new_text = existing + line
    new_lines = new_text.strip().split("\n")

    if len(new_lines) > _LOG_MAX_LINES:
        # Roll to archive
        archive_path = root / _LOG_ARCHIVE
        archive = ""
        if archive_path.exists():
            archive = archive_path.read_text(encoding="utf-8")
        archive += "\n".join(new_lines[:-_LOG_MAX_LINES]) + "\n"
        _atomic_write(archive_path, archive)
        new_text = "\n".join(new_lines[-_LOG_MAX_LINES:]) + "\n"

    _atomic_write(log_path, new_text)


# ---------------------------------------------------------------------------
# Profile change tracking
# ---------------------------------------------------------------------------


def append_history(root: Path, change: str) -> None:
    """Append a change record to profile/history.md."""
    history_path = root / "profile" / _HISTORY_FILE
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    line = f"- {ts}: {change}\n"
    existing = ""
    if history_path.exists():
        existing = history_path.read_text(encoding="utf-8")
    _atomic_write(history_path, existing + line)


# ---------------------------------------------------------------------------
# Disposition config
# ---------------------------------------------------------------------------


def read_disposition(root: Path) -> dict[str, int]:
    """Read per-project disposition config (config.md frontmatter).

    Returns defaults if file doesn't exist or is malformed.
    """
    defaults = {"skepticism": 3, "recency_bias": 3, "verbosity": 3}
    config_path = root / "config.md"
    if not config_path.exists():
        return defaults
    try:
        text = config_path.read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(text)
        for key in defaults:
            val = fm.get(key)
            if isinstance(val, int) and 1 <= val <= 5:
                defaults[key] = val
    except Exception:
        logger.warning("Failed to parse disposition config: %s", config_path)
    return defaults
