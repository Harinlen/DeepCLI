from __future__ import annotations

from pydantic import BaseModel, Field


class KernelFlags(BaseModel):
    """Which optional subsystems are enabled.

    Managed by FlagManager itself, not by any subsystem.  Core
    subsystems (connection_auth / provider / session) are deliberately absent —
    they cannot be disabled.
    """

    memory: bool = Field(True, description="Enable memory subsystem")
    mcp: bool = Field(True, description="Enable MCP subsystem")
    skills: bool = Field(True, description="Enable skills subsystem")
    hooks: bool = Field(True, description="Enable hooks subsystem")
    tools: bool = Field(True, description="Enable tools subsystem")
    commands: bool = Field(True, description="Enable commands subsystem")
    gateways: bool = Field(True, description="Enable gateways subsystem")
    schedule: bool = Field(True, description="Enable schedule/cron subsystem")
    git: bool = Field(True, description="Enable git subsystem")
