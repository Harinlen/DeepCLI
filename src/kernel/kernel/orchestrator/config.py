"""User-visible Orchestrator configuration schemas."""

from __future__ import annotations

from dataclasses import dataclass

from kernel.llm.config import ModelRef


@dataclass(frozen=True)
class OrchestratorConfig:
    """User-visible snapshot of the Orchestrator's current configuration.

    This deliberately excludes internal execution knobs such as compaction
    thresholds.  Session clients can display or update these fields without
    learning the private query-loop tuning surface.
    """

    # Provider/model pair resolved by LLMManager before the next stream call.
    model: ModelRef
    # ``None`` means "use provider default" instead of forcing a kernel value.
    temperature: float | None = None
    # Enables speculative tool execution only for tools marked concurrency-safe.
    streaming_tools: bool = False
    # Optional natural-language hint injected into the system prompt.
    language: str | None = None


@dataclass
class OrchestratorConfigPatch:
    """Partial config update applied by ``Orchestrator.set_config()``.

    ``None`` means "leave the current value unchanged" for every field.  Use a
    full ``OrchestratorConfig`` when a caller needs to express an explicit
    nullable value in the future.
    """

    model: ModelRef | None = None
    temperature: float | None = None
    streaming_tools: bool | None = None
    language: str | None = None
