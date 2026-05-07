"""DeepCLI runtime-control ACP extension schemas."""

from __future__ import annotations

from typing import Any

from kernel.protocol.acp.schemas.base import AcpModel


class RuntimeStatusRequest(AcpModel):
    meta: dict[str, Any] | None = None


class RuntimeStatusResponse(AcpModel):
    status: dict[str, Any]
    meta: dict[str, Any] | None = None


class RuntimeRestartRequest(AcpModel):
    reason: str = "user requested runtime restart"
    meta: dict[str, Any] | None = None


class RuntimeRestartResponse(AcpModel):
    status: dict[str, Any]
    meta: dict[str, Any] | None = None
