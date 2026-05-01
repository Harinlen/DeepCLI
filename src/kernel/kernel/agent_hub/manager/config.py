"""ConfigManager section schema for durable AgentDefinitions."""

from __future__ import annotations

from pydantic import BaseModel, Field

from kernel.agents import AgentDefinition


class AgentDefinitionsConfig(BaseModel):
    """Config-owned durable agent declarations."""

    agents: tuple[AgentDefinition, ...] = Field(default_factory=tuple)
