"""Tests for :mod:`kernel.config.loader`.

Loader is all pure functions, so the tests exercise each layer in
isolation (``deep_merge``, CLI parsing, file scanning) plus an
integration case that folds all four priorities together.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kernel.config import loader


# --------------------------------------------------------------------
# deep_merge
# --------------------------------------------------------------------


def test_deep_merge_recurses_on_dicts() -> None:
    low = {"a": {"x": 1, "y": 2}, "b": 1}
    high = {"a": {"y": 20, "z": 30}}
    merged = loader.deep_merge(low, high)

    assert merged == {"a": {"x": 1, "y": 20, "z": 30}, "b": 1}
    # Input dicts must not be mutated.
    assert low == {"a": {"x": 1, "y": 2}, "b": 1}


def test_deep_merge_replaces_lists_wholesale() -> None:
    low = {"items": [1, 2, 3]}
    high = {"items": [9]}
    assert loader.deep_merge(low, high) == {"items": [9]}


def test_deep_merge_high_none_overrides_low_value() -> None:
    # ``null`` in the higher layer is an explicit "unset" and should
    # replace whatever the lower layer had.
    assert loader.deep_merge({"x": 1}, {"x": None}) == {"x": None}


def test_deep_merge_type_mismatch_high_wins() -> None:
    assert loader.deep_merge({"x": {"nested": 1}}, {"x": 5}) == {"x": 5}
    assert loader.deep_merge({"x": 5}, {"x": {"nested": 1}}) == {"x": {"nested": 1}}


def test_deep_merge_preserves_keys_not_touched_by_high() -> None:
    low = {"a": 1, "b": {"c": 2, "d": 3}}
    high = {"b": {"c": 20}}
    assert loader.deep_merge(low, high) == {"a": 1, "b": {"c": 20, "d": 3}}


# --------------------------------------------------------------------
# load_file_raw
# --------------------------------------------------------------------


def test_load_file_raw_missing_returns_empty(tmp_path: Path) -> None:
    assert loader.load_file_raw(tmp_path / "does-not-exist.yaml") == {}


def test_load_file_raw_empty_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("")
    assert loader.load_file_raw(path) == {}


def test_load_file_raw_non_mapping_raises(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- 1\n- 2\n")
    with pytest.raises(ValueError, match="mapping"):
        loader.load_file_raw(path)


# --------------------------------------------------------------------
# parse_cli_overrides
# --------------------------------------------------------------------


def test_parse_cli_overrides_builds_nested_dict() -> None:
    out = loader.parse_cli_overrides(
        [
            "config.tools.bash_timeout=30",
            "config.tools.browser=true",
            "config.provider.model=gpt-4",
        ]
    )
    assert out == {
        "config": {
            "tools": {"bash_timeout": 30, "browser": True},
            "provider": {"model": "gpt-4"},
        },
    }


def test_parse_cli_overrides_skips_malformed() -> None:
    out = loader.parse_cli_overrides(
        [
            "no-equals-sign",
            "too.many.dots.here=1",
            "missing.parts=",
            "config.tools.bash=true",
        ]
    )
    # Only the well-formed override survives.
    assert out == {"config": {"tools": {"bash": True}}}


# --------------------------------------------------------------------
# collect (integration across all five layers)
# --------------------------------------------------------------------


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True))


def test_collect_missing_dirs_yields_empty(tmp_path: Path) -> None:
    out = loader.collect(
        global_dir=tmp_path / "global",  # doesn't exist
        project_dir=tmp_path / "project",  # doesn't exist
        cli_overrides=(),
    )
    assert out == {}


def test_collect_global_plus_project_plus_local(tmp_path: Path) -> None:
    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"

    _write_yaml(
        global_dir / "config.yaml",
        {"tools": {"bash": True, "bash_timeout": 60}, "mcp": {"enabled": True}},
    )
    _write_yaml(
        project_dir / "config.yaml",
        {"tools": {"bash_timeout": 120}},
    )
    _write_yaml(
        project_dir / "config.local.yaml",
        {"tools": {"browser": True}},
    )

    out = loader.collect(
        global_dir=global_dir,
        project_dir=project_dir,
        cli_overrides=(),
    )

    # Single ``config`` bucket with all three layers merged.
    assert out == {
        "config": {
            "tools": {
                "bash": True,
                "bash_timeout": 120,  # project layer wins over global
                "browser": True,  # project-local adds a field
            },
            "mcp": {"enabled": True},  # global untouched
        }
    }


def test_collect_cli_overrides_beat_files(tmp_path: Path) -> None:
    global_dir = tmp_path / "global"
    _write_yaml(
        global_dir / "config.yaml",
        {"tools": {"bash_timeout": 60}},
    )

    out = loader.collect(
        global_dir=global_dir,
        project_dir=tmp_path / "project",
        cli_overrides=("config.tools.bash_timeout=999",),
    )
    assert out["config"]["tools"]["bash_timeout"] == 999


def test_collect_separate_files_stay_separate(tmp_path: Path) -> None:
    global_dir = tmp_path / "global"
    _write_yaml(global_dir / "config.yaml", {"a": {"x": 1}})
    _write_yaml(global_dir / "mcp.yaml", {"b": {"y": 2}})

    out = loader.collect(
        global_dir=global_dir,
        project_dir=tmp_path / "project",
        cli_overrides=(),
    )
    assert out == {
        "config": {"a": {"x": 1}},
        "mcp": {"b": {"y": 2}},
    }


def test_collect_cli_overrides_materialize_missing_file_buckets(
    tmp_path: Path,
) -> None:
    """A CLI override for ``new_file.section.key`` should create the
    bucket even if ``new_file.yaml`` does not exist on disk."""
    out = loader.collect(
        global_dir=tmp_path / "global",
        project_dir=tmp_path / "project",
        cli_overrides=("new_file.section.key=42",),
    )
    assert out == {"new_file": {"section": {"key": 42}}}
