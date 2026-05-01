"""HOOK.md frontmatter parsing — happy path + every error mode."""

from __future__ import annotations

from pathlib import Path

import pytest

from kernel.hooks.manifest import (
    HookRequires,
    ManifestError,
    parse_manifest,
)


def _write_hook(
    base: Path,
    *,
    name: str = "demo",
    md: str | None = None,
    handler: str = "async def handle(ctx):\n    pass\n",
) -> Path:
    """Create a hook directory with HOOK.md + handler.py.

    ``md`` defaults to a minimal valid manifest.  Pass ``""`` to write
    an empty file or a custom string for negative cases.
    """
    hook_dir = base / name
    hook_dir.mkdir()
    if md is None:
        md = (
            "---\n"
            f"name: {name}\n"
            "description: test\n"
            "events: [user_prompt_submit]\n"
            "---\n"
            "# body\n"
        )
    (hook_dir / "HOOK.md").write_text(md, encoding="utf-8")
    (hook_dir / "handler.py").write_text(handler, encoding="utf-8")
    return hook_dir


def test_minimal_manifest_parses(tmp_path: Path) -> None:
    hook_dir = _write_hook(tmp_path)
    m = parse_manifest(hook_dir)

    assert m.name == "demo"
    assert m.description == "test"
    assert m.events == ("user_prompt_submit",)
    assert m.requires == HookRequires()
    assert m.os == ()
    assert m.base_dir == hook_dir
    assert m.handler_path == hook_dir / "handler.py"


def test_name_defaults_to_directory_when_missing(tmp_path: Path) -> None:
    hook_dir = _write_hook(
        tmp_path,
        name="my-hook",
        md=("---\nevents: [stop]\n---\n"),
    )
    assert parse_manifest(hook_dir).name == "my-hook"


def test_full_metadata_parses(tmp_path: Path) -> None:
    md = (
        "---\n"
        "name: full\n"
        "description: kitchen sink\n"
        "events: [pre_tool_use, post_tool_use]\n"
        "requires:\n"
        "  bins: [git, jq]\n"
        "  env: [HOME]\n"
        "os: [linux, darwin]\n"
        "---\n"
    )
    hook_dir = _write_hook(tmp_path, md=md)
    m = parse_manifest(hook_dir)

    assert m.events == ("pre_tool_use", "post_tool_use")
    assert m.requires.bins == ("git", "jq")
    assert m.requires.env == ("HOME",)
    assert m.os == ("linux", "darwin")


def test_unknown_keys_silently_ignored(tmp_path: Path) -> None:
    """Forward-compat: extra YAML keys do not crash the parser."""
    md = (
        "---\n"
        "events: [stop]\n"
        "future_field: whatever\n"
        "metadata: {claude: irrelevant}\n"
        "---\n"
    )
    hook_dir = _write_hook(tmp_path, md=md)
    m = parse_manifest(hook_dir)
    assert m.events == ("stop",)


def test_missing_hook_md_raises(tmp_path: Path) -> None:
    hook_dir = tmp_path / "no-md"
    hook_dir.mkdir()
    (hook_dir / "handler.py").write_text("async def handle(ctx): pass\n")
    with pytest.raises(ManifestError, match="missing HOOK.md"):
        parse_manifest(hook_dir)


def test_missing_handler_py_raises(tmp_path: Path) -> None:
    hook_dir = tmp_path / "no-handler"
    hook_dir.mkdir()
    (hook_dir / "HOOK.md").write_text("---\nevents: [stop]\n---\n")
    with pytest.raises(ManifestError, match="missing handler.py"):
        parse_manifest(hook_dir)


def test_missing_opening_fence_raises(tmp_path: Path) -> None:
    hook_dir = _write_hook(tmp_path, md="not a frontmatter\nbody\n")
    with pytest.raises(ManifestError, match="expected '---' on first line"):
        parse_manifest(hook_dir)


def test_missing_closing_fence_raises(tmp_path: Path) -> None:
    hook_dir = _write_hook(tmp_path, md="---\nevents: [stop]\nno close\n")
    with pytest.raises(ManifestError, match="closing '---' not found"):
        parse_manifest(hook_dir)


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    hook_dir = _write_hook(tmp_path, md="---\nevents: [unclosed\n---\n")
    with pytest.raises(ManifestError, match="YAML parse error"):
        parse_manifest(hook_dir)


def test_frontmatter_not_mapping_raises(tmp_path: Path) -> None:
    hook_dir = _write_hook(tmp_path, md="---\n- a list\n- not a map\n---\n")
    with pytest.raises(ManifestError, match="must be a YAML mapping"):
        parse_manifest(hook_dir)


def test_missing_events_raises(tmp_path: Path) -> None:
    hook_dir = _write_hook(tmp_path, md="---\nname: x\n---\n")
    with pytest.raises(ManifestError, match="'events' must be a non-empty list"):
        parse_manifest(hook_dir)


def test_empty_events_raises(tmp_path: Path) -> None:
    hook_dir = _write_hook(tmp_path, md="---\nevents: []\n---\n")
    with pytest.raises(ManifestError, match="'events' must be a non-empty list"):
        parse_manifest(hook_dir)


def test_events_with_non_string_raises(tmp_path: Path) -> None:
    hook_dir = _write_hook(tmp_path, md="---\nevents: [42]\n---\n")
    with pytest.raises(ManifestError, match="'events' entries must be non-empty strings"):
        parse_manifest(hook_dir)


def test_requires_must_be_mapping(tmp_path: Path) -> None:
    md = "---\nevents: [stop]\nrequires: not-a-mapping\n---\n"
    hook_dir = _write_hook(tmp_path, md=md)
    with pytest.raises(ManifestError, match="'requires' must be a mapping"):
        parse_manifest(hook_dir)
