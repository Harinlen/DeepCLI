"""Runtime flags for the session subsystem."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SessionFlags(BaseModel):
    """Session subsystem flags. Runtime-immutable."""

    max_queue_length: int = Field(
        50,
        ge=1,
        le=10000,
        description="Max queued prompts per session before rejecting new ones",
    )
    list_page_size: int = Field(
        50,
        ge=1,
        le=500,
        description="Default page size for session/list",
    )
    tool_result_inline_limit: int = Field(
        8 * 1024,
        ge=512,
        le=1024 * 1024,
        description="Tool results larger than this (bytes) spill to a sidecar file",
    )
    enable_auto_title: bool = Field(
        True,
        description="Auto-generate session title from the first turn",
    )
