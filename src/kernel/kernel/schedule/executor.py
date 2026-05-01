"""CronExecutor — spawns isolated sessions to run cron task prompts.

Design reference: ``docs/plans/schedule-manager.md`` § 3.3 CronExecutor.

Each cron fire creates a fresh session via ``SessionManager``, injects
the task's prompt, and waits for the orchestrator to complete.  A
concurrent heartbeat loop refreshes ``running_heartbeat`` every 30 s
so the multi-instance claim protocol knows the executor is alive.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from kernel.schedule.types import CronExecution, CronTask

if TYPE_CHECKING:
    from kernel.session import SessionManager

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S: float = 30.0


class CronExecutor:
    """Runs cron tasks in isolated sessions.

    The executor is stateless — it delegates session creation and
    prompt execution to ``SessionManager`` and records the outcome
    as a ``CronExecution``.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        delivery_router: Any | None = None,
        *,
        heartbeat_fn: Any | None = None,
        hooks: Any | None = None,
    ) -> None:
        self._session_manager = session_manager
        self._delivery_router = delivery_router
        self._heartbeat_fn = heartbeat_fn  # scheduler._heartbeat_task
        self._hooks = hooks  # HookManager | None

    async def execute(self, task: CronTask) -> CronExecution:
        """Execute a cron task end-to-end.

        Steps:
            1. Create an isolated session
            2. Start heartbeat loop
            3. Inject prompt via ``run_turn_for_gateway``
            4. Wait for completion or timeout
            5. Cancel heartbeat
            6. Collect results
            7. Return CronExecution record
        """
        execution_id = uuid.uuid4().hex[:8]
        started_at = time.time()
        execution = CronExecution(
            id=execution_id,
            task_id=task.id,
            session_id="",  # filled after session creation
            started_at=started_at,
        )

        heartbeat_task: asyncio.Task[None] | None = None
        session_id: str | None = None

        try:
            # Fire pre_cron_fire hook
            enriched_prompt = task.prompt
            if self._hooks:
                try:
                    from pathlib import Path

                    from kernel.hooks.types import AmbientContext, HookEvent, HookEventCtx

                    ctx = HookEventCtx(
                        event=HookEvent.PRE_CRON_FIRE,
                        ambient=AmbientContext(
                            session_id="",
                            cwd=Path.cwd(),
                            agent_depth=0,
                            mode="default",
                            timestamp=time.time(),
                        ),
                        tool_name=f"cron:{task.id}",
                    )
                    await self._hooks.fire(ctx)
                    # If hook produced stdout via messages, prepend to prompt
                    if ctx.messages:
                        prefix = "\n".join(ctx.messages)
                        enriched_prompt = f"[Pre-run data]\n{prefix}\n\n{task.prompt}"
                except Exception:
                    logger.debug("pre_cron_fire hook error — continuing", exc_info=True)

            # Step 1: Create isolated session
            session_id = await self._session_manager.create_for_gateway(
                instance_id=f"cron:{task.id}",
                peer_id="cron-executor",
            )
            execution.session_id = session_id

            # Step 2: Start heartbeat
            if self._heartbeat_fn:
                heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(task.id),
                    name=f"cron-heartbeat-{task.id}",
                )

            # Step 3+4: Inject prompt and wait
            async def _auto_approve(req: Any) -> Any:
                """Auto-approve all permission requests in cron context."""
                from kernel.orchestrator.types import PermissionResponse

                return PermissionResponse(
                    decision="allow_once",
                    updated_input=None,
                )

            reply = await asyncio.wait_for(
                self._session_manager.run_turn_for_gateway(
                    session_id=session_id,
                    text=enriched_prompt,
                    on_permission=_auto_approve,
                ),
                timeout=task.timeout_seconds,
            )

            # Step 6: Success
            ended_at = time.time()
            execution.ended_at = ended_at
            execution.duration_ms = (ended_at - started_at) * 1000
            execution.status = "completed"
            execution.summary = reply[:2000] if reply else None
            execution.stop_reason = "end_turn"

        except asyncio.TimeoutError:
            ended_at = time.time()
            execution.ended_at = ended_at
            execution.duration_ms = (ended_at - started_at) * 1000
            execution.status = "timeout"
            execution.error = f"Execution timed out after {task.timeout_seconds}s"
            logger.warning(
                "Cron task %s timed out after %.0fs",
                task.id,
                task.timeout_seconds,
            )

        except Exception as exc:
            ended_at = time.time()
            execution.ended_at = ended_at
            execution.duration_ms = (ended_at - started_at) * 1000
            execution.status = "failed"
            execution.error = str(exc)[:2000]
            logger.exception("Cron task %s failed", task.id)

        finally:
            # Step 5: Cancel heartbeat
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

        # Fire post_cron_fire hook
        if self._hooks:
            try:
                from pathlib import Path

                from kernel.hooks.types import AmbientContext, HookEvent, HookEventCtx

                ctx = HookEventCtx(
                    event=HookEvent.POST_CRON_FIRE,
                    ambient=AmbientContext(
                        session_id="",
                        cwd=Path.cwd(),
                        agent_depth=0,
                        mode="default",
                        timestamp=time.time(),
                    ),
                    tool_name=f"cron:{task.id}",
                )
                ctx.messages = []  # make messages available
                await self._hooks.fire(ctx)
            except Exception:
                logger.debug("post_cron_fire hook error — continuing", exc_info=True)

        # Step 7: Deliver results
        if self._delivery_router is not None:
            try:
                d_status, d_error = await self._delivery_router.deliver(task, execution)
                execution.delivery_status = d_status
                execution.delivery_error = d_error
            except Exception:
                logger.exception("Delivery failed for cron %s", task.id)
                execution.delivery_status = "not-delivered"

        return execution

    async def _heartbeat_loop(self, task_id: str) -> None:
        """Refresh ``running_heartbeat`` every 30 s until cancelled."""
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)
                if self._heartbeat_fn:
                    await self._heartbeat_fn(task_id)
        except asyncio.CancelledError:
            pass
