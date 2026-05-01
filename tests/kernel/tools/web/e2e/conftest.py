"""E2E test configuration — real network requests."""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: real network requests (deselect with -m 'not e2e')")


def skip_without_key(*env_vars: str):
    """Skip the test if any of the given env vars are missing."""
    missing = [v for v in env_vars if not os.getenv(v, "").strip()]
    if missing:
        return pytest.mark.skipif(
            True,
            reason=f"Missing env var(s): {', '.join(missing)}",
        )
    return pytest.mark.skipif(False, reason="")
