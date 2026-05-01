"""Unit tests for WebFetchTool — permission, validation, risk."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kernel.tools.builtin.web_fetch import WebFetchTool
from kernel.tools.types import ToolInputError


@pytest.fixture
def tool():
    return WebFetchTool()


@pytest.fixture
def ctx():
    c = MagicMock()
    c.cwd = Path.cwd()
    c.session_id = "s-1"
    return c


# ── default_risk ──


def test_risk_preapproved(tool, ctx):
    s = tool.default_risk({"url": "https://docs.python.org/3/"}, ctx)
    assert s.default_decision == "allow"
    assert s.risk == "low"


def test_risk_unknown_host(tool, ctx):
    s = tool.default_risk({"url": "https://sketchy.example.com/"}, ctx)
    assert s.default_decision == "ask"
    assert s.risk == "medium"


def test_risk_github(tool, ctx):
    s = tool.default_risk({"url": "https://github.com/user/repo"}, ctx)
    assert s.default_decision == "allow"


# ── permission matcher ──


def test_matcher_matches_pattern(tool):
    matcher = tool.prepare_permission_matcher(
        {"url": "https://api.github.com/repos"}
    )
    assert matcher("*.github.com")


def test_matcher_rejects_wrong_pattern(tool):
    matcher = tool.prepare_permission_matcher(
        {"url": "https://api.github.com/repos"}
    )
    assert not matcher("*.evil.com")


# ── validate_input ──


async def test_validate_rejects_empty_url(tool, ctx):
    with pytest.raises(ToolInputError):
        await tool.validate_input({"url": ""}, ctx)


async def test_validate_rejects_ftp(tool, ctx):
    with pytest.raises(ToolInputError):
        await tool.validate_input({"url": "ftp://bad.com/file"}, ctx)


async def test_validate_rejects_ssrf(tool, ctx):
    with pytest.raises(ToolInputError):
        await tool.validate_input({"url": "http://169.254.169.254/"}, ctx)


async def test_validate_allows_https(tool, ctx):
    # Should not raise
    await tool.validate_input({"url": "https://example.com/"}, ctx)


# ── activity description ──


def test_activity_description(tool):
    desc = tool.activity_description({"url": "https://docs.python.org/3/"})
    assert "docs.python.org" in desc
