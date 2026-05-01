"""Layered configuration loader — pure functions, no state.

Turns the three-layer directory structure plus CLI overrides into a
single ``dict[file_stem, raw_dict]`` that ConfigManager can then slice
per-section at ``bind_section`` time.

Priority order (low → high, later wins in :func:`deep_merge`):

1. Global user layer       ``~/.mustang/config/<file>.yaml``
2. Project layer           ``<cwd>/.mustang/config/<file>.yaml``
3. Project local layer     ``<cwd>/.mustang/config/<file>.local.yaml``
4. CLI overrides           ``--config <file>.<section>.<key>=<val>``

Schema defaults (layer 0 in the design doc) are applied inside
ConfigManager when a section is bound, not here — we only deal with
raw YAML data.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def deep_merge(low: Any, high: Any) -> Any:
    """Merge ``high`` onto ``low``, dict-recursive, leaves-replace.

    - Two dicts recurse key-by-key.  Keys present only in ``low`` are
      preserved, keys present in both take the ``high`` value (after
      recursion).
    - Any leaf type (list, str, int, bool, None, ...) is replaced
      wholesale by ``high``.  Lists are never concatenated — if the
      user wants to "append", they rewrite the full list at the higher
      layer, or model the data as a dict keyed by id.
    - If ``low`` and ``high`` have incompatible types (e.g. dict vs
      list), ``high`` wins outright.
    """
    if not isinstance(low, dict) or not isinstance(high, dict):
        return high
    result: dict[Any, Any] = dict(low)
    for key, value in high.items():
        if key in result:
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_file_raw(path: Path) -> dict[str, Any]:
    """Read a single YAML file into a dict.

    Missing file → empty dict (the layer just contributes nothing).
    Top-level non-mapping or malformed YAML → :class:`ValueError`,
    because silently ignoring a corrupt config file would hide bugs.
    """
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(
            f"{path} must contain a YAML mapping at the top level, got {type(data).__name__}"
        )
    return data


def parse_cli_overrides(
    overrides: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Parse ``<file>.<section>.<key>=<value>`` strings.

    Each override comes from a ``--config`` flag on the kernel CLI.
    Format is strict: exactly three dot-separated components before
    the ``=``, non-empty on each side.  Malformed overrides are
    logged and skipped.
    """
    out: dict[str, dict[str, Any]] = {}
    for raw in overrides:
        if "=" not in raw:
            logger.warning(
                "Ignoring malformed CLI override %r — expected <file>.<section>.<key>=<value>",
                raw,
            )
            continue
        lhs, _, rhs = raw.partition("=")
        parts = lhs.split(".")
        if len(parts) != 3 or not all(parts):
            logger.warning(
                "Ignoring malformed CLI override %r — expected <file>.<section>.<key>=<value>",
                raw,
            )
            continue
        file_key, section_key, field_key = parts
        value = _parse_scalar(rhs)
        file_bucket = out.setdefault(file_key, {})
        section_bucket = file_bucket.setdefault(section_key, {})
        section_bucket[field_key] = value
    return out


def collect(
    *,
    global_dir: Path,
    project_dir: Path,
    cli_overrides: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Merge every layer into ``{file_stem: merged_raw_dict}``.

    File discovery is driven by filesystem contents — any ``*.yaml``
    under ``global_dir`` / ``project_dir`` contributes.  A file named
    ``foo.local.yaml`` goes into the ``foo`` bucket at the project
    local layer, not as a distinct ``foo.local`` key.

    CLI overrides can target files that don't exist on disk; they will
    simply materialize a new file bucket that subsystems can still
    ``bind_section`` against.
    """
    merged: dict[str, dict[str, Any]] = {}

    # Layers 1-3: directory globs, lowest priority first.
    layers: list[tuple[Path, bool]] = [
        (global_dir, False),
        (project_dir, False),
        (project_dir, True),
    ]
    for directory, want_local in layers:
        for file_stem, raw in _scan_layer(directory, local=want_local):
            current = merged.get(file_stem)
            merged[file_stem] = deep_merge(current, raw) if current is not None else raw

    # Layer 4: CLI overrides (highest priority).
    for file_stem, patch in parse_cli_overrides(cli_overrides).items():
        current = merged.get(file_stem)
        merged[file_stem] = deep_merge(current, patch) if current is not None else patch

    return merged


def _scan_layer(directory: Path, *, local: bool) -> list[tuple[str, dict[str, Any]]]:
    """Yield ``(file_stem, raw_dict)`` for every YAML in ``directory``.

    When ``local=True`` only ``*.local.yaml`` files are considered and
    the ``.local`` suffix is stripped so they merge into the same
    bucket as the non-local version.  When ``local=False`` the
    ``*.local.yaml`` files are skipped entirely.
    """
    if not directory.is_dir():
        return []
    results: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.yaml")):
        name = path.name
        is_local = name.endswith(".local.yaml")
        if local != is_local:
            continue
        stem = name[: -len(".local.yaml")] if is_local else path.stem
        if not stem:
            continue
        results.append((stem, load_file_raw(path)))
    return results


def _parse_scalar(raw: str) -> Any:
    """Best-effort YAML scalar parse, falling back to the raw string.

    Keeps ``"1.2.3"`` and other non-YAML strings intact while still
    letting ``"true"`` become ``True``, ``"30"`` become ``30``, etc.
    """
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw
    if parsed is None and raw.strip().lower() not in {"null", "~", ""}:
        # Don't let a stray newline or unparseable junk collapse to None.
        return raw
    return parsed
