"""Streaming response events emitted by the Orchestrator.

Text and thinking are separated because providers treat hidden reasoning as a
different payload class; downstream clients can render or suppress it without
parsing provider-specific chunks.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextDelta:
    """One streaming text chunk from the LLM.

    Chunks preserve provider streaming cadence; batching and coalescing happen
    later in the client-stream layer.
    """

    content: str


@dataclass(frozen=True)
class ThoughtDelta:
    """One streaming reasoning chunk from the LLM.

    These chunks are also preserved in history when the provider requires
    replaying signed thinking blocks on later turns.
    """

    content: str
