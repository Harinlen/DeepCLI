"""SKILL.md frontmatter parsing + supporting file discovery.

A skill directory contains at minimum one file:

- ``SKILL.md`` — Markdown body with a YAML frontmatter block

And optionally supporting files (references, templates, scripts)
that the LLM can load on demand via the ``Read`` tool.

The parser is intentionally permissive: unknown frontmatter keys are
silently dropped for forward-compatibility.  Anything malformed at
YAML or schema level raises :class:`ManifestError` so the loader can
skip the offending skill without crashing the kernel.

Frontmatter schema is a superset of Claude Code's
``parseSkillFrontmatterFields`` + Hermes extensions.
See ``docs/plans/landed/skill-manager.md`` § Frontmatter schema.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Literal

import yaml

from kernel.skills.types import (
    SkillFallbackFor,
    SkillManifest,
    SkillRequires,
    SkillSetup,
    SkillSetupEnvVar,
)

logger = logging.getLogger(__name__)

# Frontmatter delimiter — must appear on the very first line and again
# to close the block.
_FENCE = "---"

_SKILL_FILENAME = "SKILL.md"


class ManifestError(ValueError):
    """Raised on malformed SKILL.md or invalid metadata."""


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def parse_skill_manifest(skill_dir: Path) -> SkillManifest:
    """Read ``skill_dir/SKILL.md`` and return a parsed :class:`SkillManifest`.

    Raises :class:`ManifestError` on:

    - Missing ``SKILL.md``
    - Missing / unclosed frontmatter fence
    - YAML parse errors

    Unknown keys are silently ignored for forward-compatibility.
    ``description`` falls back to the first ``#`` heading or paragraph
    in the body when not specified in frontmatter.
    """
    md_path = skill_dir / _SKILL_FILENAME
    if not md_path.is_file():
        raise ManifestError(f"missing {_SKILL_FILENAME} in {skill_dir}")

    text = md_path.read_text(encoding="utf-8")
    raw = _extract_frontmatter(text, source=md_path)
    body = strip_frontmatter(text)

    # -- name --
    name = raw.get("name") or skill_dir.name
    if not isinstance(name, str):
        raise ManifestError(f"{md_path}: 'name' must be a string")
    name = str(name).strip()

    # -- description --
    raw_desc = raw.get("description")
    if raw_desc is not None:
        description = str(raw_desc).strip()
        has_user_specified_description = True
    else:
        description = _extract_description_from_body(body)
        has_user_specified_description = False

    # -- allowed-tools --
    allowed_tools = tuple(
        _coerce_str_list(raw.get("allowed-tools") or [], field_name="allowed-tools", source=md_path)
    )

    # -- argument-hint --
    argument_hint = _opt_str(raw, "argument-hint", md_path)

    # -- arguments --
    arguments_raw = raw.get("arguments")
    argument_names: tuple[str, ...]
    if isinstance(arguments_raw, str):
        argument_names = (arguments_raw,)
    elif isinstance(arguments_raw, list):
        argument_names = tuple(
            _coerce_str_list(arguments_raw, field_name="arguments", source=md_path)
        )
    else:
        argument_names = ()

    # -- when-to-use (accept both kebab and snake) --
    when_to_use = _opt_str(raw, "when-to-use", md_path) or _opt_str(raw, "when_to_use", md_path)

    # -- booleans --
    user_invocable = _parse_bool(raw, "user-invocable", default=True)
    disable_model_invocation = _parse_bool(raw, "disable-model-invocation", default=False)

    # -- requires --
    requires = _parse_requires(raw.get("requires"), md_path)

    # -- fallback-for (Hermes) --
    fallback_for = _parse_fallback_for(raw.get("fallback-for"), md_path)

    # -- os --
    os_list = tuple(_coerce_str_list(raw.get("os") or [], field_name="os", source=md_path))

    # -- context / agent / model --
    context_raw = raw.get("context")
    context: Literal["inline", "fork"] | None = "fork" if context_raw == "fork" else None
    agent = _opt_str(raw, "agent", md_path)
    model = _opt_str(raw, "model", md_path)

    # -- hooks --
    hooks_raw = raw.get("hooks")
    hooks = dict(hooks_raw) if isinstance(hooks_raw, dict) else None

    # -- paths (conditional activation globs) --
    paths_raw = raw.get("paths")
    if isinstance(paths_raw, list):
        paths: tuple[str, ...] | None = tuple(
            _coerce_str_list(paths_raw, field_name="paths", source=md_path)
        )
        if not paths:
            paths = None
    else:
        paths = None

    # -- setup (Hermes) --
    setup = _parse_setup(raw.get("setup"), md_path)

    # -- config (Hermes) --
    config_raw = raw.get("config")
    config = dict(config_raw) if isinstance(config_raw, dict) else None

    # -- supporting files --
    supporting_files = _discover_supporting_files(skill_dir)

    return SkillManifest(
        name=name,
        description=description,
        has_user_specified_description=has_user_specified_description,
        allowed_tools=allowed_tools,
        argument_hint=argument_hint,
        argument_names=argument_names,
        when_to_use=when_to_use,
        user_invocable=user_invocable,
        disable_model_invocation=disable_model_invocation,
        requires=requires,
        fallback_for=fallback_for,
        os=os_list,
        context=context,
        agent=agent,
        model=model,
        hooks=hooks,
        paths=paths,
        setup=setup,
        config=config,
        base_dir=skill_dir,
        supporting_files=supporting_files,
    )


def strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter (``---`` delimited) from *text*,
    returning only the Markdown body.

    Returns the original text unchanged if no frontmatter is found.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FENCE:
        return text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == _FENCE:
            return "".join(lines[idx + 1 :]).lstrip("\n")
    # Unclosed frontmatter — return everything after the opening fence.
    return "".join(lines[1:])


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------


def _extract_frontmatter(text: str, *, source: Path) -> dict[str, Any]:
    """Pull the YAML frontmatter block out of a SKILL.md text."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise ManifestError(f"{source}: expected '{_FENCE}' on first line of {_SKILL_FILENAME}")
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


def _extract_description_from_body(body: str) -> str:
    """Extract a description from the Markdown body when frontmatter
    omits ``description``.

    Strategy: first ``# Heading`` line, or first non-empty paragraph.
    """
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Strip leading # to get heading text.
        heading_match = re.match(r"^#+\s+(.*)", stripped)
        if heading_match:
            return heading_match.group(1).strip()
        # First non-empty, non-heading line.
        return stripped[:200]
    return "Skill"


def _coerce_str_list(value: object, *, field_name: str, source: Path) -> list[str]:
    """Validate that ``value`` is a list of strings; reject anything else.

    Accepts ``[]`` (empty list).  Aligns with ``hooks/manifest.py``.
    """
    if isinstance(value, str):
        # Single string → wrap in list (convenience for YAML scalars).
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        raise ManifestError(f"{source}: '{field_name}' must be a list")
    out: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not str(entry).strip():
            raise ManifestError(
                f"{source}: '{field_name}' entries must be non-empty strings, got {entry!r}"
            )
        out.append(str(entry).strip())
    return out


def _opt_str(raw: dict[str, Any], key: str, source: Path) -> str | None:
    """Read an optional string field from frontmatter."""
    val = raw.get(key)
    if val is None:
        return None
    if not isinstance(val, (str, int, float)):
        raise ManifestError(f"{source}: '{key}' must be a string")
    return str(val).strip() or None


def _parse_bool(raw: dict[str, Any], key: str, *, default: bool) -> bool:
    """Parse a boolean frontmatter field with a default."""
    val = raw.get(key)
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "yes", "1")
    return default


def _parse_requires(raw: object, source: Path) -> SkillRequires:
    """Parse the ``requires`` frontmatter block."""
    if not raw or not isinstance(raw, dict):
        return SkillRequires()
    return SkillRequires(
        bins=tuple(
            _coerce_str_list(raw.get("bins") or [], field_name="requires.bins", source=source)
        ),
        env=tuple(_coerce_str_list(raw.get("env") or [], field_name="requires.env", source=source)),
        tools=tuple(
            _coerce_str_list(raw.get("tools") or [], field_name="requires.tools", source=source)
        ),
        toolsets=tuple(
            _coerce_str_list(
                raw.get("toolsets") or [], field_name="requires.toolsets", source=source
            )
        ),
    )


def _parse_fallback_for(raw: object, source: Path) -> SkillFallbackFor | None:
    """Parse the ``fallback-for`` frontmatter block (Hermes)."""
    if not raw or not isinstance(raw, dict):
        return None
    tools = tuple(
        _coerce_str_list(raw.get("tools") or [], field_name="fallback-for.tools", source=source)
    )
    toolsets = tuple(
        _coerce_str_list(
            raw.get("toolsets") or [], field_name="fallback-for.toolsets", source=source
        )
    )
    if not tools and not toolsets:
        return None
    return SkillFallbackFor(tools=tools, toolsets=toolsets)


def _parse_setup(raw: object, source: Path) -> SkillSetup | None:
    """Parse the ``setup`` frontmatter block (Hermes)."""
    if not raw or not isinstance(raw, dict):
        return None
    env_raw = raw.get("env")
    if not env_raw or not isinstance(env_raw, list):
        return None
    entries: list[SkillSetupEnvVar] = []
    for item in env_raw:
        if not isinstance(item, dict):
            logger.warning(
                "%s: setup.env entry must be a mapping, got %s", source, type(item).__name__
            )
            continue
        name = item.get("name")
        if not name or not isinstance(name, str):
            logger.warning("%s: setup.env entry missing 'name'", source)
            continue
        entries.append(
            SkillSetupEnvVar(
                name=str(name).strip(),
                prompt=str(item.get("prompt", f"Enter {name}")).strip(),
                help=str(item["help"]).strip() if item.get("help") else None,
                secret=bool(item.get("secret", False)),
                optional=bool(item.get("optional", False)),
                default=str(item["default"]).strip() if item.get("default") is not None else None,
            )
        )
    if not entries:
        return None
    return SkillSetup(env=tuple(entries))


def _discover_supporting_files(skill_dir: Path) -> tuple[str, ...]:
    """Recursively list non-SKILL.md files in the skill directory.

    Returns relative paths sorted alphabetically.  These are listed
    in the activation message so the LLM can ``Read`` them on demand
    (progressive disclosure — Hermes pattern).
    """
    files: list[str] = []
    try:
        for path in skill_dir.rglob("*"):
            if path.is_file() and path.name != _SKILL_FILENAME:
                files.append(str(path.relative_to(skill_dir)))
    except OSError:
        pass
    return tuple(sorted(files))


__all__ = ["ManifestError", "parse_skill_manifest", "strip_frontmatter"]
