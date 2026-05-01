"""ACP wire-format schemas for ``secrets/auth`` method."""

from __future__ import annotations

from typing import Literal

from kernel.protocol.acp.schemas.base import AcpModel


class AuthRequest(AcpModel):
    """``secrets/auth`` request params."""

    action: Literal["set", "get", "list", "delete", "import_env"]
    name: str | None = None
    value: str | None = None
    kind: str | None = None
    env_var: str | None = None


class AuthResult(AcpModel):
    """``secrets/auth`` response."""

    value: str | None = None
    names: list[str] | None = None
    ok: bool = True
