"""Gateways subsystem — external messaging platform integrations."""

from __future__ import annotations

from kernel.gateways.base import GatewayAdapter, InboundMessage
from kernel.gateways.manager import GatewayManager

__all__ = ["GatewayAdapter", "GatewayManager", "InboundMessage"]
