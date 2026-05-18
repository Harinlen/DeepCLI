"""In-process test adapter for Access Router probes."""

from __future__ import annotations

from kernel.access_router.adapters.base import AccessAdapter, AdapterInboundMessage, AdapterReply


class TestAccessAdapter(AccessAdapter):
    """Deterministic adapter used by unit tests and probes."""

    __test__ = False

    def __init__(self, adapter_id: str = "test") -> None:
        self.adapter_id = adapter_id
        self.started = False
        self.fail_start = False
        self.sent: list[AdapterReply] = []

    async def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("test adapter startup failed")
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def send(self, reply: AdapterReply) -> None:
        self.sent.append(reply)

    def inbound(self, *, channel_key: str, external_message_id: str, text: str) -> AdapterInboundMessage:
        return AdapterInboundMessage(
            adapter_id=self.adapter_id,
            channel_key=channel_key,
            external_message_id=external_message_id,
            text=text,
        )
