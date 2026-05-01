"""SessionGrantCache — per-session memory + signature algorithm."""

from __future__ import annotations

from unittest.mock import MagicMock

from kernel.tool_authz.session_grant_cache import (
    SessionGrantCache,
    compute_signature,
)


def _fake_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    return tool


def test_grant_then_check_hit() -> None:
    cache = SessionGrantCache()
    cache.on_session_open("s-1")
    tool = _fake_tool("Bash")
    inp = {"command": "git status"}

    cache.grant(session_id="s-1", tool=tool, tool_input=inp)
    hit = cache.check(session_id="s-1", tool=tool, tool_input=inp)
    assert hit is not None
    assert hit.signature == compute_signature(tool, inp)


def test_check_miss_returns_none() -> None:
    cache = SessionGrantCache()
    cache.on_session_open("s-1")
    tool = _fake_tool("Bash")
    assert cache.check(session_id="s-1", tool=tool, tool_input={"command": "ls"}) is None


def test_grants_are_scoped_per_session() -> None:
    cache = SessionGrantCache()
    cache.on_session_open("s-1")
    cache.on_session_open("s-2")
    tool = _fake_tool("Bash")
    inp = {"command": "git status"}

    cache.grant(session_id="s-1", tool=tool, tool_input=inp)
    assert cache.check(session_id="s-1", tool=tool, tool_input=inp) is not None
    assert cache.check(session_id="s-2", tool=tool, tool_input=inp) is None


def test_session_close_wipes_grants() -> None:
    cache = SessionGrantCache()
    cache.on_session_open("s-1")
    tool = _fake_tool("Bash")
    inp = {"command": "ls"}
    cache.grant(session_id="s-1", tool=tool, tool_input=inp)
    cache.on_session_close("s-1")
    assert cache.check(session_id="s-1", tool=tool, tool_input=inp) is None


def test_signature_is_exact_match_not_prefix() -> None:
    """Same argv first token but different suffix → different signatures."""
    tool = _fake_tool("Bash")
    sig_a = compute_signature(tool, {"command": "npm install"})
    sig_b = compute_signature(tool, {"command": "npm install -g"})
    assert sig_a != sig_b


def test_signature_stable_across_dict_ordering() -> None:
    """Key ordering must not affect the signature."""
    tool = _fake_tool("FileEdit")
    sig_a = compute_signature(tool, {"path": "/a", "old_string": "x", "new_string": "y"})
    sig_b = compute_signature(tool, {"new_string": "y", "old_string": "x", "path": "/a"})
    assert sig_a == sig_b
