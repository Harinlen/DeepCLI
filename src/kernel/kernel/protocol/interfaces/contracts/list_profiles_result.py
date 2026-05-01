"""ListProfilesResult — contract type returned by model/profile_list."""

from __future__ import annotations

from pydantic import BaseModel


class ProfileInfo(BaseModel):
    """Metadata for one registered model profile."""

    name: str
    """User-chosen logical name (e.g. ``"claude-opus"``)."""

    provider_type: str
    """Provider backend type (e.g. ``"anthropic"``)."""

    model_id: str
    """Actual API model identifier (e.g. ``"claude-opus-4-6"``)."""

    is_default: bool
    """Whether this profile is the current kernel default."""


class ListProfilesResult(BaseModel):
    """Result of a model/profile_list operation."""

    profiles: list[ProfileInfo]
    """All registered profiles, ordered by insertion."""

    default_model: str
    """The current default model name (key or alias)."""
