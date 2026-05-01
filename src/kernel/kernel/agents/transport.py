"""Internal Agent Hub transport envelopes.

B0 defines encode/decode contracts only.  This module deliberately avoids
FastAPI and WebSocket server wiring so the Hub can remain transport-neutral.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


HUB_FRAME_SCHEMA_VERSION = "hub-frame.b0"


class HubFrameType(StrEnum):
    """Internal Agent Hub frame categories."""

    REQUEST = "request"
    RESPONSE = "response"
    EVENT = "event"


class HubFrame(BaseModel):
    """Versioned internal frame exchanged over Hub loopback WebSocket."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    frame_id: str
    frame_type: HubFrameType
    contract: str
    schema_version: str = HUB_FRAME_SCHEMA_VERSION
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None

    def to_json_bytes(self) -> bytes:
        """Encode the frame for WebSocket binary/text transport tests."""

        return self.model_dump_json().encode("utf-8")

    @classmethod
    def from_json_bytes(cls, data: bytes | str) -> "HubFrame":
        """Decode a frame without depending on any FastAPI route."""

        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return cls.model_validate_json(data)
