"""HOOK.md frontmatter parsing.

A hook directory contains exactly two files (convention is fixed,
no customization — see ``docs/plans/landed/hook-manager.md`` §7.2.1):

- ``HOOK.md`` — markdown body with a YAML frontmatter block
- ``handler.py`` — Python module exporting ``handle(ctx)``

Frontmatter shape (only ``events`` is required)::

    ---
    name: my-hook                   # optional; defaults to dir name
    description: short blurb        # optional
    events: [user_prompt_submit]    # required, non-empty
    requires:                       # optional
      bins: [git]                   # all must be on PATH
      env: [OPENAI_API_KEY]         # all must be set
    os: [linux, darwin]             # optional allow-list
    ---

The parser is intentionally permissive: unknown frontmatter keys are
silently dropped.  Anything malformed at YAML or schema level raises
:class:`ManifestError` so the loader can skip the offending hook
without crashing the kernel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Frontmatter delimiter — must appear at the very top of HOOK.md and again
# to close the block.  Anything between them is parsed as YAML.
_FENCE = "---"


class ManifestError(ValueError):
    """Raised on malformed HOOK.md or invalid metadata."""


@dataclass(frozen=True)
class HookRequires:
    """Eligibility predicates applied at load time.

    Empty lists mean "no requirement" — eligibility check passes
    trivially.  See :mod:`kernel.hooks.eligibility` for the runtime
    matcher.
    """

    bins: tuple[str, ...] = ()
    """Binaries that must resolve via ``shutil.which`` for the hook
    to load (all-must-match)."""

    env: tuple[str, ...] = ()
    """Environment variables that must be set (and non-empty) for the
    hook to load (all-must-match)."""


@dataclass(frozen=True)
class HookManifest:
    """Parsed HOOK.md metadata + paths to the hook files."""

    name: str
    """Hook id; defaults to the containing directory name when not
    set explicitly in frontmatter."""

    description: str
    events: tuple[str, ...]
    """Raw event identifiers from frontmatter; the loader maps each
    to ``HookEvent`` and rejects unknown values."""

    requires: HookRequires = field(default_factory=HookRequires)
    os: tuple[str, ...] = ()
    """OS allow-list using ``sys.platform`` values (``"linux"``,
    ``"darwin"``, ``"win32"``).  Empty = any OS."""

    base_dir: Path = field(default_factory=Path)
    """Absolute path to the hook directory."""

    handler_path: Path = field(default_factory=Path)
    """Absolute path to ``handler.py`` (always ``base_dir / "handler.py"``)."""


def parse_manifest(hook_dir: Path) -> HookManifest:
    """Read ``hook_dir/HOOK.md`` and return a parsed :class:`HookManifest`.

    Raises :class:`ManifestError` on:

    - Missing ``HOOK.md`` or ``handler.py``
    - Missing / unclosed frontmatter fence
    - YAML parse errors
    - Required ``events`` field missing or empty
    - Type errors in known fields

    Unknown keys are silently ignored to keep frontmatter forward-compatible.
    """
    md_path = hook_dir / "HOOK.md"
    handler_path = hook_dir / "handler.py"
    if not md_path.is_file():
        raise ManifestError(f"missing HOOK.md in {hook_dir}")
    if not handler_path.is_file():
        raise ManifestError(f"missing handler.py in {hook_dir}")

    text = md_path.read_text(encoding="utf-8")
    raw = _extract_frontmatter(text, source=md_path)

    events_raw = raw.get("events")
    if not events_raw or not isinstance(events_raw, list):
        raise ManifestError(f"{md_path}: 'events' must be a non-empty list")
    events = tuple(_coerce_str_list(events_raw, field_name="events", source=md_path))

    name = raw.get("name") or hook_dir.name
    if not isinstance(name, str):
        raise ManifestError(f"{md_path}: 'name' must be a string")

    description = raw.get("description") or ""
    if not isinstance(description, str):
        raise ManifestError(f"{md_path}: 'description' must be a string")

    requires_raw = raw.get("requires") or {}
    if not isinstance(requires_raw, dict):
        raise ManifestError(f"{md_path}: 'requires' must be a mapping")
    requires = HookRequires(
        bins=tuple(
            _coerce_str_list(
                requires_raw.get("bins") or [], field_name="requires.bins", source=md_path
            )
        ),
        env=tuple(
            _coerce_str_list(
                requires_raw.get("env") or [], field_name="requires.env", source=md_path
            )
        ),
    )

    os_raw = raw.get("os") or []
    os_list = tuple(_coerce_str_list(os_raw, field_name="os", source=md_path))

    return HookManifest(
        name=name,
        description=description,
        events=events,
        requires=requires,
        os=os_list,
        base_dir=hook_dir,
        handler_path=handler_path,
    )


def _extract_frontmatter(text: str, *, source: Path) -> dict[str, object]:
    """Pull the YAML frontmatter block out of a HOOK.md text.

    The frontmatter must start on the very first line with ``---`` and
    end with another ``---``.  Returns an empty dict when the YAML
    block parses to ``None`` (an empty frontmatter is allowed but
    ``parse_manifest`` will still reject because ``events`` is required).
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise ManifestError(f"{source}: expected '{_FENCE}' on first line of HOOK.md")
    # Find the closing fence; first match after line 0.
    end = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == _FENCE:
            end = idx
            break
    if end == -1:
        raise ManifestError(f"{source}: closing '{_FENCE}' not found")
    yaml_body = "\n".join(lines[1:end])
    try:
        parsed = yaml.safe_load(yaml_body) or {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"{source}: YAML parse error: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ManifestError(
            f"{source}: frontmatter must be a YAML mapping, got {type(parsed).__name__}"
        )
    return parsed


def _coerce_str_list(value: object, *, field_name: str, source: Path) -> list[str]:
    """Validate that ``value`` is a list of strings; reject anything else.

    Accepts ``[]`` (empty list).  Used for ``events`` / ``requires.bins``
    / ``requires.env`` / ``os``.
    """
    if not isinstance(value, list):
        raise ManifestError(f"{source}: '{field_name}' must be a list")
    out: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise ManifestError(
                f"{source}: '{field_name}' entries must be non-empty strings, got {entry!r}"
            )
        out.append(entry.strip())
    return out


__all__ = ["HookManifest", "HookRequires", "ManifestError", "parse_manifest"]
