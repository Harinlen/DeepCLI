"""Unit tests for WebSearchTool — permission, validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kernel.tools.builtin.web_search import WebSearchTool
from kernel.tools.types import ToolInputError


@pytest.fixture
def tool():
    return WebSearchTool()


@pytest.fixture
def ctx():
    c = MagicMock()
    c.cwd = Path.cwd()
    c.session_id = "s-1"
    return c


# ── default_risk ──


def test_risk_always_allow(tool, ctx):
    s = tool.default_risk({"query": "python"}, ctx)
    assert s.default_decision == "allow"
    assert s.risk == "low"


# ── validate_input ──


async def test_validate_rejects_empty_query(tool, ctx):
    with pytest.raises(ToolInputError):
        await tool.validate_input({"query": ""}, ctx)


async def test_validate_rejects_short_query(tool, ctx):
    with pytest.raises(ToolInputError):
        await tool.validate_input({"query": "x"}, ctx)


async def test_validate_allows_normal_query(tool, ctx):
    await tool.validate_input({"query": "python programming"}, ctx)


# ── activity description ──


def test_activity_description(tool):
    desc = tool.activity_description({"query": "python programming"})
    assert "python" in desc


def test_activity_description_truncates(tool):
    long_query = "a" * 100
    desc = tool.activity_description({"query": long_query})
    assert "..." in desc
    assert len(desc) < 100
