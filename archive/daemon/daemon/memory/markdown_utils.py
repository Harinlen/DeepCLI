"""Markdown helpers for the memory store.

Pure string-level utilities extracted from :mod:`daemon.memory.store`
so they can be unit-tested (and reused) without pulling the whole
MemoryStore machinery.  Re-exported from ``store.py`` for
backward compatibility.

Helpers:

- :func:`split_frontmatter` — separate a YAML ``---`` header from
  the body.
- :func:`append_to_section` — add a bullet under an H2 heading,
  creating the section when it doesn't exist yet.
- :func:`parse_memory_file` — load + validate frontmatter from disk.
- :func:`serialize_memory_file` — produce ``---frontmatter---\\n\\nbody``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from daemon.memory.schema import MemoryFrontmatter

_FRONTMATTER_DELIM = "---"


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split ``---``-delimited YAML frontmatter from the body.

    Args:
        text: Full file contents, possibly with a YAML frontmatter
            header.

    Returns:
        ``(frontmatter_str, body)`` when the delimiters are present,
        otherwise ``(None, original_text)``.  The delimiters
        themselves are stripped from the returned frontmatter.
    """
    stripped = text.lstrip()
    if not stripped.startswith(_FRONTMATTER_DELIM):
        return None, text
    after_first = stripped[len(_FRONTMATTER_DELIM) :]
    closing = after_first.find(f"\n{_FRONTMATTER_DELIM}")
    if closing == -1:
        return None, text
    fm = after_first[:closing].strip()
    rest = after_first[closing + len(f"\n{_FRONTMATTER_DELIM}") :]
    nl = rest.find("\n")
    body = rest[nl + 1 :] if nl != -1 else ""
    return fm, body


def append_to_section(body: str, section: str, bullet: str) -> str:
    """Append ``- <bullet>`` under ``## <section>``; create section if absent.

    The section match is case-sensitive on the exact heading text.
    Bullets are appended to the end of the section (just before the
    next ``## `` heading, or the end of file).

    Args:
        body: Existing markdown body text.
        section: Section name (without the ``## `` prefix).
        bullet: Bullet body text (without the leading ``- ``).

    Returns:
        New body text with the bullet inserted and a trailing newline.
    """
    section_heading = f"## {section}"
    lines = body.splitlines()

    # Find section heading.
    section_idx = -1
    for i, line in enumerate(lines):
        if line.strip() == section_heading:
            section_idx = i
            break

    if section_idx == -1:
        # Append new section at end (with leading blank line if body non-empty).
        new_lines = list(lines)
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append(section_heading)
        new_lines.append(f"- {bullet}")
        return "\n".join(new_lines) + "\n"

    # Find end of section (next "## " line, or end of body).
    end_idx = len(lines)
    for j in range(section_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end_idx = j
            break

    # Insert new bullet at end of section, trimming trailing blank lines.
    new_bullet = f"- {bullet}"
    pre = lines[: section_idx + 1]
    section_body = lines[section_idx + 1 : end_idx]
    post = lines[end_idx:]

    while section_body and section_body[-1].strip() == "":
        section_body.pop()
    section_body.append(new_bullet)

    # Preserve a blank line before next section if there is one.
    out = pre + section_body
    if post:
        out.append("")  # blank line before next ##
        out.extend(post)
    return "\n".join(out) + "\n"


def parse_memory_file(path: Path) -> tuple[MemoryFrontmatter, str]:
    """Read and parse a ``.md`` memory file from disk.

    Args:
        path: Filesystem path to a memory markdown file.

    Returns:
        ``(frontmatter, body)`` parsed from the file contents.

    Raises:
        ValueError: If the file has no YAML frontmatter or the
            frontmatter is not a mapping.
    """
    text = path.read_text(encoding="utf-8")
    fm_str, body = split_frontmatter(text)
    if fm_str is None:
        raise ValueError(f"{path}: missing YAML frontmatter")
    meta = yaml.safe_load(fm_str) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: frontmatter is not a mapping")
    return MemoryFrontmatter(**meta), body


def serialize_memory_file(fm: MemoryFrontmatter, body: str) -> str:
    """Produce a full ``---frontmatter---\\n\\nbody\\n`` memory file.

    Keys are emitted in a deterministic order for diff-friendliness.

    Args:
        fm: Parsed frontmatter model.
        body: Markdown body text (trailing whitespace is stripped
            and a single ``\\n`` is appended).

    Returns:
        Full file contents ready to write to disk.
    """
    fm_dict = {
        "name": fm.name,
        "description": fm.description,
        "type": fm.type.value,
        "kind": fm.kind.value,
    }
    fm_text = yaml.safe_dump(fm_dict, sort_keys=False, allow_unicode=True).rstrip()
    body_stripped = body.rstrip() + "\n"
    return f"{_FRONTMATTER_DELIM}\n{fm_text}\n{_FRONTMATTER_DELIM}\n\n{body_stripped}"


__all__ = [
    "append_to_section",
    "parse_memory_file",
    "serialize_memory_file",
    "split_frontmatter",
]
