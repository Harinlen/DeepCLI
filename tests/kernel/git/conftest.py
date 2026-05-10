"""Shared fixtures for GitManager lifecycle tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio

from kernel.agents.mustang.git import GitManager


@pytest_asyncio.fixture(autouse=True)
async def close_git_managers(monkeypatch: Any) -> AsyncIterator[None]:
    """Close every GitManager a test creates.

    Most git tests exercise startup paths directly instead of going
    through the kernel lifecycle.  This fixture gives those tests the
    same shutdown discipline the real subsystem table provides.
    """
    managers: list[GitManager] = []
    original_init = GitManager.__init__

    def tracked_init(self: GitManager, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        managers.append(self)

    monkeypatch.setattr(GitManager, "__init__", tracked_init)
    yield

    for manager in reversed(managers):
        await manager.shutdown()
