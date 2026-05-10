"""Runtime constants for Orchestrator query execution."""

from __future__ import annotations

# Sub-agents are expected to finish bounded tasks; the high cap preserves
# compatibility with older AgentTool behavior while still preventing runaway
# child loops from living forever.
SUBAGENT_DEFAULT_MAX_TURNS = 200

# Compact before the provider's real limit so the next request still has room
# for tool schemas, system prompt volatility, and the user's new turn.
COMPACTION_FRACTION = 0.80

# Fallback only.  Real providers should supply a model-specific context window.
DEFAULT_CONTEXT_WINDOW = 200_000

# Reactive retries are for empty/provider-noise turns, not for tool failures.
MAX_REACTIVE_RETRIES = 2

# Max-token retries escalate output allowance a few times before surfacing stop.
MAX_OUTPUT_TOKEN_RETRIES = 3
MAX_TOKENS_ESCALATED = 64_000
