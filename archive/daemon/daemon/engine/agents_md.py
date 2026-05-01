"""AGENTS.md discovery (with MUSTANG.md fallback).

Walks from *cwd* upward to the filesystem root, collecting every
``AGENTS.md`` file it finds along the way, plus a global
``~/.mustang/AGENTS.md`` when present.  ``MUSTANG.md`` is accepted
at every level as a back-compat alias — new projects should use
``AGENTS.md`` (community convention shared with Codex + Claude Code).

Extracted from :mod:`daemon.engine.context` so the traversal logic
lives apart from the system-prompt assembler that consumes it.
"""

from __future__ import annotations

from pathlib import Path

# Preferred filenames first — canonical in the community (Codex,
# Claude Code).  MUSTANG.md kept as fallback so existing installs
# keep working; new projects should use AGENTS.md.
_AGENTS_MD_NAMES = ("AGENTS.md", "MUSTANG.md")
_AGENTS_DIR_NAMES = (".mustang/AGENTS.md", ".mustang/MUSTANG.md")
_GLOBAL_AGENTS_NAMES = ("AGENTS.md", "MUSTANG.md")


def discover_agents_md(cwd: Path | None = None) -> list[tuple[Path, str]]:
    """Walk from *cwd* upward to root, collecting AGENTS.md files.

    Also checks ``~/.mustang/AGENTS.md`` (global user instructions).
    ``MUSTANG.md`` is accepted as a fallback name at every level for
    backward compatibility.

    Returns:
        List of ``(path, content)`` tuples, ordered from deepest
        (closest to *cwd*) to shallowest (global).
    """
    results: list[tuple[Path, str]] = []
    seen: set[Path] = set()

    start = (cwd or Path.cwd()).resolve()
    current = start

    while True:
        for name in (*_AGENTS_MD_NAMES, *_AGENTS_DIR_NAMES):
            candidate = current / name
            resolved = candidate.resolve()
            if resolved not in seen and resolved.is_file():
                seen.add(resolved)
                results.append((resolved, resolved.read_text(encoding="utf-8")))
        parent = current.parent
        if parent == current:
            break
        current = parent

    # Global user instructions — AGENTS.md preferred, MUSTANG.md fallback.
    mustang_dir = Path.home() / ".mustang"
    for name in _GLOBAL_AGENTS_NAMES:
        global_md = mustang_dir / name
        if global_md.resolve() not in seen and global_md.is_file():
            seen.add(global_md.resolve())
            results.append((global_md.resolve(), global_md.read_text(encoding="utf-8")))

    return results


# Backward-compat alias — still used by a few callers.  New code
# should import ``discover_agents_md``.
discover_mustang_md = discover_agents_md


__all__ = ["discover_agents_md", "discover_mustang_md"]
