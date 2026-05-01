"""Public Protocol implemented by session-scoped Orchestrators."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from kernel.orchestrator.config import OrchestratorConfig, OrchestratorConfigPatch
from kernel.orchestrator.permissions import PermissionCallback
from kernel.orchestrator.stop import StopReason

if TYPE_CHECKING:
    from kernel.orchestrator.events import OrchestratorEvent
    from kernel.protocol.interfaces.contracts.content_block import ContentBlock


@runtime_checkable
class Orchestrator(Protocol):
    """Contract between the Session layer and the conversation engine.

    Implementations are session-scoped: they own one conversation history and
    expose a streaming event generator that Session can persist and translate
    into client protocol updates.
    """

    def query(
        self,
        prompt: list[ContentBlock],
        *,
        on_permission: PermissionCallback,
        token_budget: int | None = None,
        max_turns: int = 0,
    ) -> AsyncGenerator[OrchestratorEvent, None]:
        """Run one prompt turn and yield Orchestrator events.

        Args:
            prompt: User content blocks already normalized by the Session layer.
            on_permission: Callback used when ToolAuthorizer needs a user choice.
            token_budget: Optional per-call budget across prompt and completion.
            max_turns: Maximum LLM/tool loop iterations; ``0`` means unlimited.

        Returns:
            Async generator object for the turn event stream.

        Yields:
            Streaming model deltas, tool lifecycle events, and housekeeping
            events in the exact order Session should persist them.
        """
        ...

    async def close(self) -> None:
        """Tear down session-local resources held by the Orchestrator.

        Returns:
            ``None``.
        """
        ...

    def set_plan_mode(self, enabled: bool) -> None:
        """Enable or disable plan mode using the legacy boolean API.

        Args:
            enabled: ``True`` enters plan mode; ``False`` leaves it.

        Returns:
            ``None``.
        """
        ...

    def set_mode(self, mode: str) -> None:
        """Set the permission mode.

        ``"restore"`` is accepted by concrete implementations as a session-layer
        convenience for leaving plan mode and restoring the previous mode.

        Args:
            mode: Permission mode id or implementation-supported sentinel.

        Returns:
            ``None``.
        """
        ...

    def set_config(self, patch: OrchestratorConfigPatch) -> None:
        """Apply a partial config update while preserving unspecified fields.

        Args:
            patch: Config values to merge into the current snapshot.

        Returns:
            ``None``.
        """
        ...

    @property
    def mode(self) -> str:
        """Current permission mode string.

        Returns:
            Mode id used by the permission pipeline.
        """
        ...

    @property
    def plan_mode(self) -> bool:
        """Whether plan mode is currently active.

        Returns:
            ``True`` when the active mode is plan mode.
        """
        ...

    @property
    def stop_reason(self) -> StopReason:
        """Stop reason from the most recent query.

        Returns:
            Last terminal reason recorded by the query loop.
        """
        ...

    @property
    def config(self) -> OrchestratorConfig:
        """Current user-visible config snapshot.

        Returns:
            Immutable config values safe for Session broadcasts.
        """
        ...

    @property
    def last_turn_usage(self) -> tuple[int, int]:
        """Input/output tokens accumulated during the last turn.

        Returns:
            ``(input_tokens, output_tokens)`` for the most recent turn.
        """
        ...
