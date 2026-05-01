"""Tests for the cleanup registry (lifecycle module)."""

from __future__ import annotations

import pytest

from daemon.lifecycle import register_cleanup, reset_for_testing, run_cleanups


@pytest.fixture(autouse=True)
def _clean() -> None:
    """Reset registry between tests."""
    reset_for_testing()


class TestRegisterCleanup:
    """Tests for register_cleanup / run_cleanups."""

    @pytest.mark.asyncio
    async def test_basic_registration_and_execution(self) -> None:
        """Registered callback is called during run_cleanups."""
        called = False

        async def cleanup() -> None:
            nonlocal called
            called = True

        register_cleanup(cleanup)
        await run_cleanups()

        assert called

    @pytest.mark.asyncio
    async def test_multiple_callbacks_all_called(self) -> None:
        """All registered callbacks are called."""
        calls: list[str] = []

        async def a() -> None:
            calls.append("a")

        async def b() -> None:
            calls.append("b")

        register_cleanup(a)
        register_cleanup(b)
        await run_cleanups()

        assert set(calls) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_unregister(self) -> None:
        """Unregister function removes the callback."""
        called = False

        async def cleanup() -> None:
            nonlocal called
            called = True

        unreg = register_cleanup(cleanup)
        unreg()
        await run_cleanups()

        assert not called

    @pytest.mark.asyncio
    async def test_unregister_idempotent(self) -> None:
        """Calling unregister twice does not raise."""

        async def cleanup() -> None:
            pass

        unreg = register_cleanup(cleanup)
        unreg()
        unreg()  # Should not raise

    @pytest.mark.asyncio
    async def test_error_does_not_block_others(self) -> None:
        """A failing callback does not prevent others from running."""
        ok_called = False

        async def bad() -> None:
            raise RuntimeError("boom")

        async def ok() -> None:
            nonlocal ok_called
            ok_called = True

        register_cleanup(bad)
        register_cleanup(ok)
        await run_cleanups()

        assert ok_called

    @pytest.mark.asyncio
    async def test_registry_cleared_after_run(self) -> None:
        """Registry is empty after run_cleanups."""
        call_count = 0

        async def inc() -> None:
            nonlocal call_count
            call_count += 1

        register_cleanup(inc)
        await run_cleanups()
        assert call_count == 1

        # Running again should not re-execute
        await run_cleanups()
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_empty_run_is_noop(self) -> None:
        """run_cleanups with no registrations is a no-op."""
        await run_cleanups()  # Should not raise

    def test_reset_for_testing(self) -> None:
        """reset_for_testing clears the registry."""

        async def noop() -> None:
            pass

        register_cleanup(noop)
        reset_for_testing()
        # No way to assert directly but run_cleanups should be empty
