"""LLMManager configuration schema.

Loaded by ``LLMManager.startup()`` via::

    config_mgr.get_section(file="kernel", section="llm", schema=LLMConfig)

User config example (``~/.mustang/config/kernel.yaml``):

.. code-block:: yaml

    llm:
      providers:
        bedrock:
          type: bedrock
          api_key: AKIA...
          aws_secret_key: secret...
          aws_region: us-east-1
          models:
            - us.anthropic.claude-sonnet-4-6
            - id: us.anthropic.claude-haiku-4-5
              max_tokens: 4096

        anthropic:
          type: anthropic
          api_key: sk-ant-xxx
          models: null        # null -> auto-discover via /v1/models

        local-qwen:
          type: openai_compatible
          base_url: http://localhost:11434/v1
          models: null        # null -> auto-discover via /v1/models

      current_used:
        default: [anthropic, claude-opus-4-6]
        bash_judge: [bedrock, us.anthropic.claude-haiku-4-5]

      model_aliases:
        opus: [anthropic, claude-opus-4-6]
        fast: [anthropic, claude-sonnet-4-6]
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator, model_validator


class ModelSpec(BaseModel):
    """Per-model settings within a provider.

    Supports shorthand: a plain string ``"claude-opus-4-6"`` is
    normalised to ``ModelSpec(id="claude-opus-4-6")`` by the parent
    ``ProviderConfig`` validator.
    """

    id: str
    """Actual model identifier sent to the API (e.g. ``"claude-opus-4-6"``)."""

    max_tokens: int = 8192
    """Maximum tokens to request per completion."""

    thinking: bool = False
    """Enable extended thinking / reasoning (Anthropic only)."""

    prompt_caching: bool = True
    """Enable prompt caching where supported (Anthropic only)."""


class ProviderConfig(BaseModel):
    """Configuration for one named provider entry (credentials + models)."""

    type: str
    """Provider type: ``"anthropic"`` | ``"bedrock"`` | ``"openai_compatible"`` | ``"nvidia"``."""

    api_key: str | None = None
    """API key.  For ``bedrock``: AWS access key ID."""

    base_url: str | None = None
    """Custom endpoint URL.  ``None`` uses the provider default."""

    aws_secret_key: str | None = None
    """AWS secret access key.  ``bedrock`` only."""

    aws_region: str | None = None
    """AWS region (e.g. ``"us-east-1"``).  ``bedrock`` only."""

    models: list[ModelSpec] | None = None
    """Models available under this provider.

    ``None`` means "auto-discover via provider API on first use".
    An explicit list acts as a whitelist — only these models are
    exposed.  Each entry can be a plain ``str`` (normalised to
    ``ModelSpec``) or a full ``ModelSpec`` dict for per-model overrides.
    """

    @field_validator("models", mode="before")
    @classmethod
    def _normalise_models(cls, v: Any) -> list[dict[str, Any]] | None:
        if v is None:
            return None
        result: list[dict[str, Any]] = []
        for item in v:
            if isinstance(item, str):
                result.append({"id": item})
            elif isinstance(item, dict):
                result.append(item)
            else:
                # Already a ModelSpec (e.g. from code)
                result.append(item)
        return result


class ModelRef(BaseModel):
    """A (provider, model_id) pair identifying a specific model.

    Serialised as a two-element list in YAML/JSON::

        [anthropic, claude-opus-4-6]
    """

    provider: str
    """Key in ``LLMConfig.providers``."""

    model: str
    """Model identifier within that provider (matches ``ModelSpec.id``)."""

    @model_validator(mode="before")
    @classmethod
    def _from_list(cls, v: Any) -> Any:
        """Accept ``[provider, model_id]`` shorthand."""
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return {"provider": v[0], "model": v[1]}
        return v

    def to_list(self) -> list[str]:
        """Serialise back to ``[provider, model_id]`` for YAML."""
        return [self.provider, self.model]


class CurrentUsedConfig(BaseModel):
    """Role -> model ref mapping for "which model is currently in use for X".

    Values are ``ModelRef`` instances (serialised as ``[provider, model_id]``
    lists in YAML).  ``LLMManager.startup()`` resolves every non-None
    field against the provider registry and raises on unknown refs.

    New roles (e.g. ``compact``, ``vision``) drop in as additional
    fields without touching existing callers -- consumers ask for their
    role by name via ``LLMManager.model_for(role)``.
    """

    default: ModelRef
    """Fallback model when a session does not specify one explicitly."""

    bash_judge: ModelRef | None = None
    """Model used by ``ToolAuthorizer.BashClassifier`` for LLMJudge
    safety classification of ambiguous bash commands.

    ``None`` (the default) disables the LLMJudge path -- BashClassifier
    falls back to asking the user for ambiguous commands.
    """

    memory: ModelRef | None = None
    """Model used by ``MemoryManager`` for relevance scoring, background
    extraction, consolidation, and other memory operations.

    ``None`` (the default) means memory operations use ``default``.
    """

    embedding: ModelRef | None = None
    """Embedding model for vector-based memory retrieval (Phase 3).

    ``None`` (the default) disables embedding hybrid search.
    """

    compact: ModelRef | None = None
    """Small / cheap / fast model used for summarisation tasks:
    ``Compactor`` (autoCompact conversation summaries) and
    ``WebFetch`` secondary-model post-processing.

    ``None`` (the default) means these callers use ``default``.  Set to
    a haiku / mini-class model to save tokens on bulk summarisation.
    """


class LLMConfig(BaseModel):
    """Top-level config section for ``LLMManager``.

    Read from the ``llm:`` section of ``kernel.yaml``.
    """

    providers: dict[str, ProviderConfig] = {}
    """Provider configuration table.

    Keys are user-chosen logical names (e.g. ``"anthropic"``,
    ``"bedrock"``).  Values describe the provider type, credentials,
    and the models available under that provider.
    """

    current_used: CurrentUsedConfig
    """Role -> model ref mapping.  Consumed via
    ``LLMManager.model_for(role)`` by Orchestrator / Session / future
    Compactor.  Each role's value is validated at startup."""

    model_aliases: dict[str, ModelRef] = {}
    """Extra alias -> ModelRef mappings.

    Allows short names like ``opus`` to resolve to a full
    ``[anthropic, claude-opus-4-6]`` ref.
    """
