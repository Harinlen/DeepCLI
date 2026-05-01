"""Tests for the Transport ABC contract."""

from __future__ import annotations

import asyncio

import pytest

from daemon.extensions.mcp.transport.base import Transport, TransportClosed


class ConcreteTransport(Transport):
    """Minimal concrete implementation for contract testing."""

    def __init__(self) -> None:
        self._connected = False
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def connect(self) -> None:
        self._connected = True

    async def send(self, message: bytes) -> None:
        if not self._connected:
            raise TransportClosed("not connected")

    async def receive(self) -> bytes:
        if not self._connected:
            raise TransportClosed("not connected")
        return await self._queue.get()

    async def close(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected


class TestTransportContract:
    """Verify the Transport ABC contract with a concrete impl."""

    @pytest.mark.asyncio
    async def test_lifecycle(self) -> None:
        """connect → is_connected → close → not is_connected."""
        t = ConcreteTransport()
        assert not t.is_connected

        await t.connect()
        assert t.is_connected

        await t.close()
        assert not t.is_connected

    @pytest.mark.asyncio
    async def test_close_idempotent(self) -> None:
        """Calling close() twice does not raise."""
        t = ConcreteTransport()
        await t.connect()
        await t.close()
        await t.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_send_when_closed_raises(self) -> None:
        """send() on a closed transport raises TransportClosed."""
        t = ConcreteTransport()
        with pytest.raises(TransportClosed):
            await t.send(b"hello")

    @pytest.mark.asyncio
    async def test_receive_when_closed_raises(self) -> None:
        """receive() on a closed transport raises TransportClosed."""
        t = ConcreteTransport()
        with pytest.raises(TransportClosed):
            await t.receive()

    @pytest.mark.asyncio
    async def test_send_receive_roundtrip(self) -> None:
        """Data put in the queue is returned by receive()."""
        t = ConcreteTransport()
        await t.connect()

        t._queue.put_nowait(b"hello")
        result = await t.receive()
        assert result == b"hello"


class TestTransportClosed:
    """TransportClosed exception tests."""

    def test_is_exception(self) -> None:
        assert issubclass(TransportClosed, Exception)

    def test_message(self) -> None:
        exc = TransportClosed("pipe broken")
        assert str(exc) == "pipe broken"
