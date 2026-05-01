"""Tool — the abstract base class every built-in or MCP tool implements.

See ``docs/plans/landed/tool-manager.md`` § 3 for the full rationale.
Short version: Tools are **information sources** for scheduling,
authorization, and rendering; Tools are **not** decision makers outside
``call()`` itself.  Decisions live in Orchestrator (concurrency),
ToolAuthorizer (permission), and the client (rendering).

Every Tool is a singleton within ``ToolRegistry`` — no per-call
instance state.  Per-call state flows through ``ToolContext``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Literal, Protocol, TypeVar

from kernel.orchestrator.types import ToolKind
from kernel.tools.types import (
    PermissionSuggestion,
    ToolCallProgress,
    ToolCallResult,
)

if TYPE_CHECKING:
    from kernel.llm.types import ToolSchema
    from kernel.module_table import KernelModuleTable
    from kernel.prompts.manager import PromptManager
    from kernel.tools.context import ToolContext


class RiskContext(Protocol):
    """Structural type that ``Tool.default_risk`` / ``is_destructive``
    / ``prepare_permission_matcher`` consume.

    Both ``ToolContext`` (during ``call()``) and ``AuthorizeContext``
    (during ``ToolAuthorizer.authorize()``) satisfy this Protocol —
    which is deliberate: the Tool's risk judgment can run at either
    point without caring which caller is asking.

    Fields are declared as ``@property`` so that frozen dataclasses like
    ``AuthorizeContext`` pass the structural check (mypy requires
    read-only Protocol members for frozen types).
    """

    @property
    def cwd(self) -> Path: ...
    @property
    def session_id(self) -> str: ...


InputT = TypeVar("InputT", bound=dict[str, Any])
"""Tool input type — dict-like, matches what the LLM sends as JSON."""

OutputT = TypeVar("OutputT")
"""Tool's structured output type.  Returned in ``ToolCallResult.data``."""


class Tool(ABC, Generic[InputT, OutputT]):
    """Contract every tool implements.

    Subclasses override ``call()`` (the only abstract method).  Everything
    else has a sensible default; override when the Tool-specific behaviour
    diverges.

    Subclasses may also override ``build_input_schema(module_table)`` to
    compute the JSON schema from runtime state (e.g. AgentTool enumerates
    sub-agent types at startup).
    """

    # ── Identity & metadata (class attributes) ──────────────────────────
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    """Fallback description text used when ``description_key`` is unset or
    the PromptManager has no entry for that key.  Concrete tools should
    prefer ``description_key`` + ``prompts/default/tools/<name>.txt``."""

    description_key: ClassVar[str] = ""
    """PromptManager key (e.g. ``"tools/todo_write"``) resolving to the
    authoritative description text.  When set, ``get_description()``
    reads the text from PromptManager at schema-build time, so prompt
    edits are file-only (no Python churn)."""

    kind: ClassVar[ToolKind] = ToolKind.other
    aliases: ClassVar[tuple[str, ...]] = ()
    search_hint: ClassVar[str] = ""
    """3–10 word capability phrase; used by ToolSearchTool."""

    should_defer: ClassVar[bool] = False
    """When ``True`` the Tool is only surfaced by name (no schema) until
    explicitly loaded via ``ToolSearchTool``.  Phase 1: all False."""

    always_load: ClassVar[bool] = False
    """Override ``should_defer``; tool is always in the core pool."""

    cache: ClassVar[bool] = True
    """Whether the tool schema may be included in a prompt-cache prefix.
    Most tools can cache; dynamic-schema tools (AgentTool) may not."""

    max_result_size_chars: ClassVar[int] = 100_000
    """Maximum size (in characters) of the LLM-facing tool result.

    Results exceeding this limit are truncated by ``ToolExecutor`` before
    being written to conversation history.  Override in subclasses to
    set tool-specific budgets (e.g. ``FileReadTool`` may allow larger
    results than ``GrepTool``)."""

    interrupt_behavior: ClassVar[Literal["cancel", "block"]] = "block"
    """``cancel``: support abort mid-execution (Bash, Agent).
    ``block``: run to completion even if cancel requested (pure computation)."""

    input_schema: ClassVar[dict[str, Any]] = {}
    """Static JSON Schema.  Override via ``build_input_schema`` for
    dynamic schemas computed from FlagManager / ConfigManager state."""

    # ------------------------------------------------------------------
    # Identity helpers
    # ------------------------------------------------------------------

    _prompt_manager: PromptManager | None = None
    """Injected by ``ToolManager.startup`` so tools can resolve their
    description (and any future prompt text) from the central store
    instead of embedded Python strings."""

    @classmethod
    def build_input_schema(cls, module_table: KernelModuleTable) -> dict[str, Any]:
        """Return the JSON schema for ``to_schema()``.

        Called once by ``ToolRegistry.register`` at startup; result is
        cached.  Default returns the ``input_schema`` class attribute.
        """
        return cls.input_schema

    def get_description(self) -> str:
        """Resolve the description text sent to the LLM.

        Priority:
          1. ``description_key`` lookup in PromptManager (authoritative).
          2. Fall back to the ``description`` ClassVar (legacy path +
             tools that genuinely have nothing to say, e.g. aliases).

        Subclasses that need dynamic content (e.g. current month/year
        in a search tool) override this method and call
        ``self._prompt_manager.render(key, **kwargs)``.
        """
        if self.description_key and self._prompt_manager is not None:
            if self._prompt_manager.has(self.description_key):
                return self._prompt_manager.get(self.description_key)
        return self.description

    def user_facing_name(self, _input: InputT) -> str:
        """Human-readable name shown in UI spinners / permission dialogs."""
        return self.name

    def activity_description(self, _input: InputT) -> str | None:
        """Present-tense gerund for spinners, e.g. ``"Reading main.py"``.

        Default ``None`` — the caller falls back to a generic label.
        """
        return None

    @property
    def is_read_only(self) -> bool:
        """True when ``kind`` is in the read-only category.

        Used by Orchestrator to partition concurrent tool batches.
        """
        return self.kind.is_read_only

    @property
    def is_concurrency_safe(self) -> bool:
        """Whether this tool may run in parallel with same-batch tools.

        Default: read-only tools are safe.  Override when a mutating tool
        has been verified to parallelize (rare).
        """
        return self.is_read_only

    def is_destructive(self, _input: InputT) -> bool:
        """True when this specific call is **irreversible**.

        Distinct from ``kind`` — an ``edit`` tool may or may not be
        destructive depending on input (e.g. FileEdit of an untracked file
        vs. overwrite of a versioned one).

        ToolAuthorizer excludes ``allow_always`` from the PermissionAsk
        suggestions when this returns ``True`` — so destructive calls
        never get cached as session grants.

        Default: ``False``.
        """
        return False

    def destructive_warning(self, _input: InputT) -> str | None:
        """Return a human-readable warning for destructive patterns.

        Informational only — does not affect permission decisions.
        Displayed in the ``PermissionAsk`` message to help the user
        understand the risk.  ``None`` means no warning.

        Default: ``None``.
        """
        return None

    # ------------------------------------------------------------------
    # Information source for ToolAuthorizer
    # ------------------------------------------------------------------

    def default_risk(self, input: InputT, ctx: RiskContext) -> PermissionSuggestion:
        """Tool's domain-level risk judgment for this specific input.

        Consumed by ToolAuthorizer's arbitration (rules still override).
        Must be fast (O(1) or O(len(input))); called on every authorize().

        Default: low risk, ``ask`` decision, generic reason — a
        conservative fallback that forces explicit user approval.
        """
        return PermissionSuggestion(
            risk="low",
            default_decision="ask",
            reason="no tool-specific risk rule defined",
        )

    def prepare_permission_matcher(self, input: InputT):  # noqa: ANN201
        """Return a matcher closure for permission rule patterns.

        Rule DSL looks like ``"Bash(git:*)"`` — ToolAuthorizer extracts the
        pattern ``"git:*"`` and calls this method to get a closure
        ``(pattern) -> bool`` that tells it whether the pattern matches
        *this specific input*.

        Different tools match differently (Bash by argv prefix; FileEdit
        by path glob), which is why the matching logic is Tool-owned.

        Default: matcher rejects every pattern — effectively disables
        pattern-based rules for this tool.
        """
        return lambda _pattern: False

    # ------------------------------------------------------------------
    # Input validation — runs BEFORE permission check
    # ------------------------------------------------------------------

    async def validate_input(self, input: InputT, ctx: RiskContext) -> None:
        """Raise ``ToolInputError`` for malformed inputs.

        Cheap, synchronous-grade validation (no I/O).  Permission and
        hooks are the expensive steps and run after this.  Default:
        no-op.
        """

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    @abstractmethod
    def call(
        self,
        input: InputT,
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        """Run the tool.

        Yields zero or more ``ToolCallProgress`` events followed by
        exactly one terminal ``ToolCallResult``.  The generator stops
        after the ``ToolCallResult`` (subsequent yields are ignored by
        the caller).

        Long-running tools must honour ``ctx.cancel_event`` between
        work units; on cancel, raise ``asyncio.CancelledError``.
        """

    # ------------------------------------------------------------------
    # Schema / search / indexing
    # ------------------------------------------------------------------

    def to_schema(self) -> ToolSchema:
        """Build the ``ToolSchema`` sent to the LLM.

        ``ToolRegistry`` caches the resolved ``input_schema`` onto the
        instance at registration time, so this is cheap on every call.
        """
        from kernel.llm.types import ToolSchema

        resolved = getattr(self, "_cached_input_schema", None) or self.input_schema
        return ToolSchema(
            name=self.name,
            description=self.get_description(),
            input_schema=resolved,
            cache=self.cache,
        )

    def extract_search_text(self, result: ToolCallResult) -> str:
        """Flat text for client-side transcript search indexing.

        Default: concatenate the text content of ``llm_content``.  Tools
        with structured output (FileRead, Grep) may override.
        """
        parts: list[str] = []
        for block in result.llm_content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)


__all__ = ["InputT", "OutputT", "Tool"]
