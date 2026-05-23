"""Mustang skill-management ACP extension schemas."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from kernel.core.protocol.acp.schemas.base import AcpModel


class SkillRecordEntry(AcpModel):
    name: str
    source: str
    layer_priority: int
    path: str | None = None
    user_invocable: bool
    model_invocable: bool
    command: str | None = None
    aliases: list[str] = Field(default_factory=list)
    setup_needed: bool = False
    missing_bins: list[str] = Field(default_factory=list)
    missing_env: list[str] = Field(default_factory=list)
    missing_tools: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class SkillCommandEntry(AcpModel):
    name: str
    command: str
    aliases: list[str] = Field(default_factory=list)


class SkillInspectEntry(AcpModel):
    record: SkillRecordEntry
    description: str
    when_to_use: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    argument_hint: str | None = None
    supporting_files: list[str] = Field(default_factory=list)
    requires: dict[str, Any] = Field(default_factory=dict)
    setup: dict[str, Any] | None = None
    config: dict[str, Any] | None = None


class SkillsListRequest(AcpModel):
    include_commands: bool = True
    meta: dict[str, Any] | None = None


class SkillsListResponse(AcpModel):
    skills: list[SkillRecordEntry]
    commands: list[SkillCommandEntry] = Field(default_factory=list)
    meta: dict[str, Any] | None = None


class SkillsInspectRequest(AcpModel):
    name: str
    meta: dict[str, Any] | None = None


class SkillsInspectResponse(AcpModel):
    skill: SkillInspectEntry
    meta: dict[str, Any] | None = None


class SkillsRefreshRequest(AcpModel):
    reason: str | None = None
    meta: dict[str, Any] | None = None


class SkillsRefreshResponse(AcpModel):
    changed: bool
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    meta: dict[str, Any] | None = None
