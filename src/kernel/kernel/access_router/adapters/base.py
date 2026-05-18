"""Access Router adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AdapterInboundMessage:
    """External platform message normalized by an Access Router adapter."""

    adapter_id: str
    channel_key: str
    external_message_id: str
    text: str


@dataclass(frozen=True, slots=True)
class AdapterReply:
    """Outbound reply sent by an adapter."""

    channel_key: str
    outbound_reply_id: str
    text: str


class AccessAdapter(ABC):
    """Base class for Access Router-owned external adapters."""

    adapter_id: str

    @abstractmethod
    async def start(self) -> None:
        """Start adapter ingress."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop adapter ingress."""

    @abstractmethod
    async def send(self, reply: AdapterReply) -> None:
        """Send one outbound reply through the platform."""
