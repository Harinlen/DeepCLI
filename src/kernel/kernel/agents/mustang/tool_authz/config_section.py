"""ConfigManager section bound by ToolAuthorizer.

Exposed as ``permissions`` in ``config.yaml``.  Layered user / project /
local yaml files are merged by ConfigManager's loader before we see
them; after merge we treat all rules as source=USER for attribution
purposes.  True per-layer attribution would require loading raw yaml
ourselves — deferred to a future iteration.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PermissionsSection(BaseModel):
    """Rule lists + BashClassifier tuning.

    All three list fields accept the DSL strings documented in
    ``docs/plans/landed/tool-authorizer.md`` § 8.1 — e.g.
    ``"Bash(git:*)"`` or ``"mcp__slack"``.
    """

    allow: list[str] = Field(default_factory=list)
    """Rules with ``behavior=allow``."""

    deny: list[str] = Field(default_factory=list)
    """Rules with ``behavior=deny`` — these win over allow + ask when
    multiple rules match the same call."""

    ask: list[str] = Field(default_factory=list)
    """Rules with ``behavior=ask`` — forces the user into the
    ``session/request_permission`` round-trip."""

    bash_llm_judge_enabled: bool = True
    """Whether BashClassifier's LLMJudge fallback is consulted for
    medium-risk Bash calls.  Set ``False`` for fully-offline users.

    The model itself is resolved via
    ``LLMManager.model_for("bash_judge")`` — configure under
    ``llm.current_used.bash_judge`` in ``kernel.yaml``, not here.
    If the role is not configured, LLMJudge is effectively disabled
    even when this flag is True."""

    bash_llm_judge_fail_closed: bool = True
    """Behaviour when the LLM API fails:
    - ``True``: deny + ask user to retry (aligned with CC
      ``tengu_iron_gate_closed`` default).
    - ``False``: fall through to the normal ``ask`` flow."""

    bash_safe_commands: list[str] = Field(default_factory=list)
    """Extra argv first-tokens considered safe for auto-allow.

    Merged with ``BashTool.ALLOWLIST_SAFE_COMMANDS`` for simple commands
    and with ``_COMPOUND_SAFE_COMMANDS`` for compound-command
    sub-command classification.  ``DANGEROUS_PATTERNS`` still override
    these — adding ``"rm"`` here does NOT bypass the dangerous-pattern
    deny.

    Example::

        permissions:
          bash_safe_commands: [docker, terraform, kubectl]
    """


__all__ = ["PermissionsSection"]
