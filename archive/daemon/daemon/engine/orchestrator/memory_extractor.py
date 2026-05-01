"""Background memory extraction (Phase 5.7A).

Spawns a fire-and-forget sub-agent after each qualifying query to
analyse the conversation transcript and extract long-term memories.

- **Turn-interval throttling**: only runs every N turns (default 5).
- **Coalescing**: if an extraction is already in-progress, the new
  request is stashed for a single trailing run.
- **Shutdown drain**: ``drain()`` awaits all in-flight tasks with a
  soft timeout so no extraction is lost on daemon shutdown.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from daemon.engine.memory_extract_prompt import format_extract_prompt
from daemon.permissions.modes import PermissionMode
from daemon.providers.base import (
    ImageContent,
    Message,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)

if TYPE_CHECKING:
    from daemon.config.schema import MemoryAutoExtractRuntimeConfig
    from daemon.engine.orchestrator.agent_factory import AgentFactory
    from daemon.memory.store import MemoryStore

logger = logging.getLogger(__name__)


def _format_transcript(messages: list[Message]) -> str:
    """Convert conversation messages into a compact transcript."""
    lines: list[str] = []
    for msg in messages:
        parts: list[str] = []
        for block in msg.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
            elif isinstance(block, ToolUseContent):
                parts.append(f"[tool_use: {block.name}]")
            elif isinstance(block, ToolResultContent):
                parts.append("[tool_result]")
            elif isinstance(block, ImageContent):
                parts.append("[image]")
        lines.append(f"{msg.role}: {' '.join(parts)}")
    return "\n".join(lines)


class MemoryExtractor:
    """Fire-and-forget memory extraction after qualifying queries.

    Args:
        config: Auto-extract runtime config.
        session_id: For task naming.
    """

    def __init__(
        self,
        config: MemoryAutoExtractRuntimeConfig,
        session_id: str | None = None,
    ) -> None:
        self._config = config
        self._session_id = session_id
        self._in_progress: bool = False
        self._turns_since_last: int = 0
        self._pending_messages: list[Message] | None = None
        self._in_flight: set[asyncio.Task[None]] = set()

    # -- Public API --------------------------------------------------------

    def maybe_trigger(
        self,
        messages: list[Message],
        message_count: int,
        memory_store: MemoryStore | None,
        agent_factory: AgentFactory | None,
    ) -> None:
        """Check throttle counter and spawn extraction if due.

        Args:
            messages: Current conversation messages.
            message_count: Total message count.
            memory_store: Global memory store (needed by sub-agent).
            agent_factory: For spawning the extraction sub-agent.
        """
        if not self._config.enabled:
            return
        if memory_store is None or agent_factory is None:
            return
        # Skip inside sub-agents (depth > 0).
        if agent_factory.depth > 0:
            return
        if message_count < self._config.min_messages:
            return

        self._turns_since_last += 1
        if self._turns_since_last < self._config.turn_interval:
            return

        self._turns_since_last = 0
        self._spawn(messages, agent_factory)

    async def drain(self, timeout: float | None = None) -> None:
        """Await all in-flight extractions with a soft timeout.

        Called during daemon shutdown.
        """
        if not self._in_flight:
            return
        if timeout is None:
            timeout = float(self._config.drain_timeout)
        logger.info(
            "Draining %d in-flight memory extraction(s) (timeout %.0fs)",
            len(self._in_flight),
            timeout,
        )
        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(*self._in_flight, return_exceptions=True)
        except TimeoutError:
            logger.warning(
                "Memory extraction drain timed out after %.0fs, %d task(s) still running",
                timeout,
                len(self._in_flight),
            )

    # -- Internal ----------------------------------------------------------

    def _spawn(self, messages: list[Message], factory: AgentFactory) -> None:
        """Spawn extraction, coalescing if one is already running."""
        window = self._config.extract_window
        snapshot = messages[-window:] if len(messages) > window else list(messages)

        if not snapshot:
            return

        if self._in_progress:
            self._pending_messages = snapshot
            logger.debug("Extraction in progress — stashed for trailing run")
            return

        self._in_progress = True
        task = asyncio.create_task(
            self._run(snapshot, factory),
            name=f"memory-extract-{self._session_id or 'anon'}",
        )
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)
        task.add_done_callback(lambda _: self._on_done(factory))

    def _on_done(self, factory: AgentFactory) -> None:
        """Handle extraction completion — check for trailing run."""
        self._in_progress = False
        stashed = self._pending_messages
        self._pending_messages = None

        if stashed:
            logger.debug("Running trailing extraction from stashed context")
            self._in_progress = True
            task = asyncio.create_task(
                self._run(stashed, factory),
                name=f"memory-extract-trailing-{self._session_id or 'anon'}",
            )
            self._in_flight.add(task)
            task.add_done_callback(self._in_flight.discard)
            task.add_done_callback(lambda _: self._on_done(factory))

    async def _run(self, messages: list[Message], factory: AgentFactory) -> None:
        """Run the extraction sub-agent to completion."""
        if not factory.can_spawn:
            return

        transcript = _format_transcript(messages)
        prompt = format_extract_prompt(
            transcript=transcript,
            max_new_memories=self._config.max_new_memories,
        )

        child = factory.build_child(
            tools=["memory_write", "memory_append", "memory_delete", "memory_list", "file_read"],
            permission_mode=PermissionMode.BYPASS,
        )

        try:
            async with asyncio.timeout(self._config.timeout):
                async for _event in child.query(prompt):
                    pass
            logger.debug("Memory extraction completed successfully")
        except TimeoutError:
            logger.warning("Memory extraction timed out after %ds", self._config.timeout)
        except asyncio.CancelledError:
            logger.debug("Memory extraction cancelled")
            raise
        except Exception:
            logger.exception("Memory extraction failed")
