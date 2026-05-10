"""Session mode and config-option contracts.

These models describe the typed shape passed across the protocol/session
boundary.  ACP wire conversion still happens in ``protocol.acp``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ConfigOptionChoice(BaseModel):
    """One selectable value for a session config option."""

    value: str
    name: str
    description: str | None = None


class ConfigOptionDescriptor(BaseModel):
    """Full descriptor for one ACP session config option."""

    config_id: str
    name: str
    type: Literal["select"] = "select"
    current_value: str
    options: list[ConfigOptionChoice] = Field(default_factory=list)
    description: str | None = None


class SessionModeInfo(BaseModel):
    """One selectable ACP session mode."""

    id: str
    name: str
    description: str | None = None


class SessionModeState(BaseModel):
    """Available session modes plus the current mode id."""

    current_mode_id: str
    available_modes: list[SessionModeInfo] = Field(default_factory=list)
