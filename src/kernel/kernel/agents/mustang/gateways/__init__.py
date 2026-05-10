"""Gateways subsystem — external messaging platform integrations."""

from __future__ import annotations

from kernel.agents.mustang.gateways.base import GatewayAdapter, InboundMessage
from kernel.agents.mustang.gateways.manager import GatewayManager

__all__ = ["GatewayAdapter", "GatewayManager", "InboundMessage"]
