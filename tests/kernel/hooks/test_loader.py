"""Loader — discovery / opt-in gate / boundary check / handler import."""

from __future__ import annotations

import sys
from pathlib import Path

from kernel.hooks.loader import discover


def _write_hook(
    base: Path,
    name: str,
    *,
    events: list[str],
    handler_body: str = "async def handle(ctx):\n    ctx.messages.append('fired')\n",
    extra_md: str = "",
) -> Path:
    hook_dir = base / name
    hook_dir.mkdir()
    md = (
        "---\n"
        f"name: {name}\n"
        f"events: {events}\n"
        f"{extra_md}"
        "---\n"
    )
    (hook_dir / "HOOK.md").write_text(md, encoding="utf-8")
    (hook_dir / "handler.py").write_text(handler_body, encoding="utf-8")
    return hook_dir


def test_missing_user_dir_returns_empty(tmp_path: Path) -> None:
    """No ``~/.mustang/hooks`` is the common case; must not raise."""
    result = discover(
        user_dir=tmp_path / "absent",
        project_dir=None,
        project_enabled=[],
    )
    assert result == []


def test_user_layer_loads_hook(tmp_path: Path) -> None:
    user = tmp_path / "user"
    user.mkdir()
    _write_hook(user, "demo", events=["user_prompt_submit"])

    loaded = discover(user_dir=user, project_dir=None, project_enabled=[])
    assert len(loaded) == 1
    assert loaded[0].manifest.name == "demo"
    assert loaded[0].layer == "user"
    assert callable(loaded[0].handler)


def test_project_layer_requires_opt_in(tmp_path: Path) -> None:
    user = tmp_path / "user"
    user.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    _write_hook(project, "proj-hook", events=["stop"])

    # Not opted-in → not loaded.
    loaded = discover(user_dir=user, project_dir=project, project_enabled=[])
    assert loaded == []

    # Opted-in → loaded.
    loaded = discover(user_dir=user, project_dir=project, project_enabled=["proj-hook"])
    assert len(loaded) == 1
    assert loaded[0].layer == "project"


def test_unknown_event_skips_hook(tmp_path: Path) -> None:
    user = tmp_path / "user"
    user.mkdir()
    _write_hook(user, "bad-event", events=["nonexistent_event"])

    loaded = discover(user_dir=user, project_dir=None, project_enabled=[])
    assert loaded == []


def test_malformed_manifest_skips_hook(tmp_path: Path) -> None:
    user = tmp_path / "user"
    user.mkdir()
    bad = user / "broken"
    bad.mkdir()
    (bad / "HOOK.md").write_text("not a frontmatter\n")
    (bad / "handler.py").write_text("async def handle(ctx): pass\n")

    loaded = discover(user_dir=user, project_dir=None, project_enabled=[])
    assert loaded == []


def test_handler_without_handle_func_skips(tmp_path: Path) -> None:
    user = tmp_path / "user"
    user.mkdir()
    _write_hook(
        user,
        "no-fn",
        events=["stop"],
        handler_body="x = 1\n",  # no `handle` defined
    )
    loaded = discover(user_dir=user, project_dir=None, project_enabled=[])
    assert loaded == []


def test_handler_import_error_skips(tmp_path: Path) -> None:
    user = tmp_path / "user"
    user.mkdir()
    _write_hook(
        user,
        "broken-import",
        events=["stop"],
        handler_body="import this_module_does_not_exist\n",
    )
    loaded = discover(user_dir=user, project_dir=None, project_enabled=[])
    assert loaded == []


def test_eligibility_filter_skips(tmp_path: Path) -> None:
    user = tmp_path / "user"
    user.mkdir()
    _write_hook(
        user,
        "needs-missing-bin",
        events=["stop"],
        extra_md="requires:\n  bins: [absolutely-nonexistent-binary-xyz]\n",
    )
    loaded = discover(user_dir=user, project_dir=None, project_enabled=[])
    assert loaded == []


def test_skips_non_directory_entries(tmp_path: Path) -> None:
    user = tmp_path / "user"
    user.mkdir()
    (user / "stray-file.txt").write_text("ignore me")
    _write_hook(user, "real-hook", events=["stop"])

    loaded = discover(user_dir=user, project_dir=None, project_enabled=[])
    assert len(loaded) == 1
    assert loaded[0].manifest.name == "real-hook"


def test_handler_outside_hookdir_via_symlink_blocked(tmp_path: Path) -> None:
    """Boundary check: handler.py symlinked to a path outside hook dir
    must not load.  Defends against accidental cross-directory escapes
    (the in-process trust model assumes local code is benign, but a
    stray symlink still shouldn't pull in code from a sibling dir)."""
    if sys.platform == "win32":  # symlinks need elevation on Windows
        return

    user = tmp_path / "user"
    user.mkdir()
    bad = user / "leaky"
    bad.mkdir()
    (bad / "HOOK.md").write_text("---\nevents: [stop]\n---\n")

    # handler.py points outside the hook directory.
    outside = tmp_path / "outside.py"
    outside.write_text("async def handle(ctx): pass\n")
    (bad / "handler.py").symlink_to(outside)

    loaded = discover(user_dir=user, project_dir=None, project_enabled=[])
    assert loaded == []


def test_multiple_events_register_handler_for_each(tmp_path: Path) -> None:
    user = tmp_path / "user"
    user.mkdir()
    _write_hook(user, "multi", events=["pre_tool_use", "post_tool_use", "stop"])

    loaded = discover(user_dir=user, project_dir=None, project_enabled=[])
    assert len(loaded) == 1
    assert {e.value for e in loaded[0].events} == {
        "pre_tool_use",
        "post_tool_use",
        "stop",
    }
