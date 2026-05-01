"""Tool ABC and supporting types.

Defines the contract every tool must implement, plus ``ToolContext``
(execution environment) and ``ToolResult`` (uniform return value).
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from daemon.extensions.tools.file_state_cache import FileStateCache
from daemon.memory.store import MemoryStore
from daemon.providers.base import ImageContent
from daemon.side_effects import EnterPlanMode, ExitPlanMode, FileChanged, SkillActivated, TasksUpdated


class PermissionLevel(enum.Enum):
    """How dangerous a tool invocation is.

    Determines whether the client must confirm before execution:

    - ``NONE``: auto-approve (read-only operations).
    - ``PROMPT``: ask the user for confirmation.
    - ``DANGEROUS``: strong warning + confirmation.
    """

    NONE = "none"
    PROMPT = "prompt"
    DANGEROUS = "dangerous"


class ConcurrencyHint(enum.Enum):
    """Concurrency policy for tool execution within a single LLM turn.

    The orchestrator uses this hint to decide which tool calls from
    the same turn can run in parallel via :func:`asyncio.gather`:

    - ``PARALLEL``: no side effects — safe to run alongside any other
      ``PARALLEL`` or non-conflicting ``KEYED`` tool.
    - ``KEYED``: safe to run in parallel *unless* another ``KEYED``
      call in the same group shares the same concurrency key (e.g.
      two writes to the same file).
    - ``SERIAL``: must run alone — side effects are unpredictable or
      global (bash, mode switches, etc.).
    """

    PARALLEL = "parallel"
    KEYED = "keyed"
    SERIAL = "serial"


class ToolResult(BaseModel):
    """Uniform return value from every tool execution.

    Attributes:
        output: Human-readable result text (fed back to the LLM).
        is_error: Whether the execution failed.
        image_parts: Optional image blocks produced by the tool
            (e.g. FileReadTool on an image file).  Multimodal
            providers fold them into the tool_result content array;
            text-only providers drop them with a warning prefix.
        side_effect: Optional typed request asking the orchestrator to
            perform a state mutation (plan-mode switch, task-list
            broadcast, etc.).  See :mod:`daemon.side_effects`.  Kept
            out of :class:`ToolContext` so 99 % of tools that do not
            mutate orchestrator state never see it.
    """

    output: str
    is_error: bool = False
    image_parts: list[ImageContent] | None = None
    side_effect: EnterPlanMode | ExitPlanMode | TasksUpdated | SkillActivated | FileChanged | None = None
    metadata: dict[str, Any] | None = None
    """Optional rendering hints for the CLI (output_type, file_path, exit_code)."""


class ToolContext(BaseModel):
    """Execution environment passed to every tool.

    Provides tools with runtime information they need (e.g. the
    working directory).  Extended in future steps with session info,
    config, etc.

    Attributes:
        cwd: Current working directory for file/command operations.
        memory_store: Shared MemoryStore for the daemon (cross-project
            long-term memory).  Only memory_* tools touch it; all
            other tools can ignore it.  Optional so tests that
            construct ToolContext manually don't need to set it.
    """

    cwd: str
    memory_store: MemoryStore | None = None
    project_memory_store: MemoryStore | None = None
    file_state_cache: FileStateCache | None = None
    ask_user: Any | None = None  # Callable[[list[dict]], Awaitable[dict]] | None
    task_manager: Any | None = None  # TaskManager | None

    model_config = {"arbitrary_types_allowed": True}


@dataclass(frozen=True)
class ToolDescriptionContext:
    """Context passed to :meth:`Tool.get_description` for dynamic descriptions.

    Allows tools to adapt their LLM-facing description based on what
    other tools are available, whether MCP servers are connected, etc.
    """

    registered_tool_names: frozenset[str] = frozenset()
    """Names of all tools currently registered (including MCP tools)."""

    has_mcp_tools: bool = False
    """Whether any MCP-provided tools are registered."""


class Tool(ABC):
    """Abstract base class for all Mustang tools.

    Each tool declares its identity, input schema, permission level,
    and an async ``execute()`` method.

    Subclasses must:
      - Set ``name``, ``description``, ``permission_level`` class attrs.
      - Define an inner ``Input`` Pydantic model for parameter validation.
      - Implement ``execute(params, ctx)`` → ``ToolResult``.

    Validation is enforced at class creation time via
    ``__init_subclass__``.
    """

    name: str
    """Unique identifier used in LLM tool calls."""

    description: str
    """One-paragraph description shown to the LLM (static fallback)."""

    permission_level: PermissionLevel
    """Controls whether the client must confirm before execution."""

    max_result_chars: int | None = 50_000
    """Character budget for tool output.

    When the output exceeds this limit, the orchestrator truncates it
    and persists the full text to disk.  Set to ``None`` to disable
    truncation (e.g. for ``file_read`` whose output would create a
    circular persist-then-read loop).
    """

    concurrency: ConcurrencyHint = ConcurrencyHint.SERIAL
    """Concurrency policy for parallel tool execution.

    Defaults to ``SERIAL`` (safest).  Override in subclasses whose
    ``execute()`` has no side effects (``PARALLEL``) or whose side
    effects are scoped to a specific key (``KEYED``).
    """

    defer_execution: bool = False
    """If True, the orchestrator pauses tool execution and asks the user
    for approval via ``PermissionRequest`` before calling
    :meth:`execute`.

    Used for tools that represent **user decisions** (plan mode entry/
    exit, structured questions) rather than machine actions.  These
    tools bypass the permission engine entirely — they're always asked,
    never matched against allow/deny rules, and ``always_allow`` is
    intentionally not offered.

    Independent from the ``lazy`` registry classification (which
    controls schema visibility): a tool can be eager (schema sent every
    round) and still defer execution.
    """

    def get_permission_level(self, params: dict[str, Any]) -> PermissionLevel:
        """Return the effective permission level for a given invocation.

        Override in subclasses that need dynamic, per-invocation
        permission levels (e.g. BashTool classifies commands as
        read-only vs dangerous).  The default returns the static
        ``permission_level`` class attribute.

        Args:
            params: The LLM-supplied arguments dict.
        """
        return self.permission_level

    def concurrency_key(self, params: dict[str, Any]) -> str | None:
        """Return a conflict key for ``KEYED`` tools.

        Two ``KEYED`` calls with the same key are serialised; calls
        with different keys (or ``None``) may run in parallel.
        Override in ``KEYED`` tools to return the relevant scope —
        typically a file path or resource name.

        Ignored for ``PARALLEL`` and ``SERIAL`` tools.
        """
        return None

    def get_description(self, ctx: ToolDescriptionContext | None = None) -> str:
        """Return the tool description, optionally adapted to context.

        Override in subclasses that need to adjust their LLM-facing
        description based on available tools, MCP servers, etc.
        The default implementation returns the static ``description``
        class attribute.

        Args:
            ctx: Description context with info about other registered
                tools.  ``None`` means no context available (use the
                static description).
        """
        return self.description

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Validate that concrete subclasses define required attributes.

        Skips validation for intermediate abstract classes (those that
        don't define ``execute``).
        """
        super().__init_subclass__(**kwargs)

        # Skip validation for abstract intermediaries
        if getattr(cls.execute, "__isabstractmethod__", False):
            return

        for attr in ("name", "description", "permission_level"):
            if not hasattr(cls, attr) or not getattr(cls, attr):
                raise TypeError(
                    f"Tool subclass {cls.__name__} must define a non-empty '{attr}' class attribute"
                )

        if not hasattr(cls, "Input"):
            raise TypeError(
                f"Tool subclass {cls.__name__} must define an inner 'Input' Pydantic model class"
            )

    @classmethod
    def input_schema(cls) -> dict[str, Any]:
        """Return JSON Schema for this tool's parameters.

        Derived automatically from the inner ``Input`` Pydantic model
        via ``model_json_schema()``.  Works on a copy to avoid mutating
        any Pydantic-cached schema.

        Returns:
            JSON Schema dict suitable for the ``parameters`` field in a
            tool definition sent to the LLM.
        """
        schema = dict(cls.Input.model_json_schema())  # type: ignore[attr-defined]
        schema.pop("title", None)
        return schema

    @abstractmethod
    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Run the tool with validated parameters.

        Args:
            params: Raw dict from the LLM (validated against ``Input``).
            ctx: Execution context (cwd, etc.).

        Returns:
            ToolResult with output text and error flag.
        """
        ...
