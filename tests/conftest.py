"""Repository-wide pytest hygiene fixtures."""

from __future__ import annotations

import asyncio

import pytest


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown() -> None:
    """Close orphan event loops left on the default asyncio policy.

    A few sync tests and pytest-asyncio setup paths can leave a freshly
    created but never-run loop as the policy's current loop.  Closing it
    here keeps ResourceWarning-as-error runs focused on real leaks.
    """
    policy = asyncio.get_event_loop_policy()
    local = getattr(policy, "_local", None)
    loop = getattr(local, "_loop", None)
    if loop is None or loop.is_closed() or loop.is_running():
        return
    loop.close()
    asyncio.set_event_loop(None)
