"""Addressed ACP/ACPX message records for KernelBus routing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictBusModel(BaseModel):
    """Base model for closed KernelBus schemas."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class BusMessageMeta(StrictBusModel):
    """Delivery metadata for one addressed ACP/ACPX message."""

    correlation_id: str | None = Field(default=None, alias="correlationId")
    deadline_ms: int | None = Field(default=None, alias="deadlineMs")
    generation: int | None = None
    retry_attempt: int = Field(default=0, alias="retryAttempt")


class BusMessage(StrictBusModel):
    """A route header plus one raw ACP JSON-RPC payload.

    This is not a business RPC schema.  The ``acp`` field is the only
    method/result/error payload; KernelBus uses the surrounding fields only
    for routing, correlation, and delivery bookkeeping.
    """

    source: str
    target: str
    acp: dict[str, Any]
    meta: BusMessageMeta = Field(default_factory=BusMessageMeta)


class BusServiceRecord(StrictBusModel):
    """Observable topology record for one registered KernelBus target."""

    service_id: str = Field(alias="serviceId")
    kind: Literal["agent", "resource", "gateway", "client"]
    status: Literal["healthy", "degraded", "unavailable", "closed"] = "healthy"
    capabilities: tuple[str, ...] = ()
    connected: bool = True
    generation: int = 1
    owner: str | None = None
    route_ready: bool = Field(default=True, alias="routeReady")
    last_seen_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        alias="lastSeenAt",
    )
    last_error: str | None = Field(default=None, alias="lastError")


class BusTopologySnapshot(StrictBusModel):
    """KernelBus route table projection returned to Agents and probes."""

    revision: int
    services: tuple[BusServiceRecord, ...] = ()


def service_kind(service_id: str) -> Literal["agent", "resource", "gateway", "client"]:
    """Return the kind prefix for a validated service id."""

    prefix, _, rest = service_id.partition(":")
    if not rest:
        raise ValueError(f"invalid service id: {service_id!r}")
    if prefix not in {"agent", "resource", "gateway", "client"}:
        raise ValueError(f"invalid service kind: {prefix!r}")
    return prefix  # type: ignore[return-value]


__all__ = [
    "BusMessage",
    "BusMessageMeta",
    "BusServiceRecord",
    "BusTopologySnapshot",
    "service_kind",
]
