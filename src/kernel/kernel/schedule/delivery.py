"""DeliveryRouter — cron execution result delivery.

Design reference: ``docs/plans/schedule-manager.md`` § 3.3 DeliveryRouter.

Delivers cron execution results to one or more targets:
- ``"session"`` — system-reminder injection into creator session
- ``"acp"`` — ACP WebSocket broadcast (CronCompletionNotification)
- ``"gateway:<adapter>:<channel>"`` — GatewayManager announce
- ``"none"`` — skip (result only in execution record)

Supports transient retry (5s/10s/20s), idempotency cache (24h TTL),
and partial-failure-aware caching (OpenClaw design).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from kernel.schedule.errors import (
    DELIVERY_RETRY_DELAYS_S,
    is_transient_delivery_error,
)
from kernel.schedule.types import CronExecution, CronTask

if TYPE_CHECKING:
    from kernel.session import SessionManager

logger = logging.getLogger(__name__)

_IDEMPOTENCY_TTL_S: float = 24 * 3600  # 24 hours
_IDEMPOTENCY_MAX_ENTRIES: int = 2000


class DeliveryRouter:
    """Route cron execution results to configured targets.

    Instantiated by ScheduleManager; called by CronExecutor after
    each execution completes.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        gateway_manager: Any | None = None,
    ) -> None:
        self._session_manager = session_manager
        self._gateway_manager = gateway_manager
        # Idempotency cache: key → (timestamp, success)
        self._delivered: dict[str, tuple[float, bool]] = {}

    async def deliver(
        self,
        task: CronTask,
        execution: CronExecution,
    ) -> tuple[str, str | None]:
        """Deliver execution result to all configured targets.

        Returns:
            ``(delivery_status, delivery_error)`` where status is one of
            ``"delivered"``, ``"not-delivered"``, ``"skipped"``.
        """
        targets = [t.strip() for t in task.delivery.target.split(",")]
        if not targets or targets == ["none"]:
            return ("skipped", None)

        # Silent pattern check
        if (
            task.delivery.silent_pattern
            and execution.summary
            and re.search(task.delivery.silent_pattern, execution.summary)
        ):
            logger.debug(
                "Cron %s: silent pattern matched — skipping delivery",
                task.id,
            )
            return ("skipped", None)

        # Skip delivery for failed executions unless on_failure is set
        is_failure = execution.status in ("failed", "timeout")
        if is_failure and not task.delivery.on_failure:
            return ("skipped", None)

        # Idempotency check
        idem_key = f"{execution.id}:{task.delivery.target}"
        cached = self._delivered.get(idem_key)
        if cached and cached[1]:
            return ("delivered", None)

        # Build message
        message = self._format_message(task, execution)

        # Deliver to each target
        all_ok = True
        first_error: str | None = None
        for target in targets:
            target = target.strip()
            if target == "none":
                continue
            try:
                if target == "session":
                    await self._deliver_session(task, execution, message)
                elif target == "acp":
                    await self._deliver_acp(task, execution, message)
                elif target.startswith("gateway:"):
                    await self._deliver_gateway(target, message)
                else:
                    logger.warning("Unknown delivery target: %s", target)
            except Exception as exc:
                all_ok = False
                err_str = str(exc)
                if first_error is None:
                    first_error = err_str
                logger.warning(
                    "Delivery to %s failed for cron %s: %s",
                    target,
                    task.id,
                    err_str,
                )

        # Cache only full success (OpenClaw design: partial failure → no cache
        # so retry can re-attempt failed targets)
        if all_ok:
            self._delivered[idem_key] = (time.time(), True)
            self._prune_cache()
            return ("delivered", None)
        return ("not-delivered", first_error)

    async def deliver_alert(
        self,
        task: CronTask,
        error: str,
    ) -> None:
        """Send a failure alert notification (OpenClaw CronFailureAlert).

        Dispatched to the target configured in ``task.failure_alert``.
        """
        if not task.failure_alert:
            return
        alert_target = task.failure_alert.target
        message = (
            f"⚠ Cron job {task.id} ({task.description or 'unnamed'}) "
            f"has failed {task.consecutive_failures} times consecutively.\n"
            f"Last error: {error[:500]}"
        )
        try:
            if alert_target == "session" and task.session_id:
                self._inject_reminder(task.session_id, message)
            elif alert_target.startswith("gateway:"):
                await self._deliver_gateway(alert_target, message)
        except Exception:
            logger.exception("Failed to deliver failure alert for cron %s", task.id)

    # ------------------------------------------------------------------
    # Target implementations
    # ------------------------------------------------------------------

    async def _deliver_session(
        self,
        task: CronTask,
        execution: CronExecution,
        message: str,
    ) -> None:
        """Inject result as system-reminder into creator session."""
        if not task.session_id:
            logger.debug("No creator session for cron %s — skipping session delivery", task.id)
            return

        await self._retry_transient(
            lambda: self._inject_reminder_async(task.session_id, message)  # type: ignore[arg-type]
        )

    async def _inject_reminder_async(self, session_id: str, message: str) -> None:
        """Inject a system-reminder into a session's pending buffer."""
        self._inject_reminder(session_id, message)

    def _inject_reminder(self, session_id: str, message: str) -> None:
        """Synchronous reminder injection into session pending_reminders."""
        # Access session internals to queue a reminder
        session = self._session_manager._sessions.get(session_id)
        if session is not None:
            session.pending_reminders.append(f"<cron-result>\n{message}\n</cron-result>")

    async def _deliver_acp(
        self,
        task: CronTask,
        execution: CronExecution,
        message: str,
    ) -> None:
        """Broadcast CronCompletionNotification to WS clients.

        Sends to all connections on the creator session.
        """
        if not task.session_id:
            return
        session = self._session_manager._sessions.get(task.session_id)
        if session is None:
            return

        # Build a simple notification dict and broadcast
        notification = {
            "type": "cron_completion",
            "task_id": task.id,
            "execution_id": execution.id,
            "status": execution.status,
            "summary": (execution.summary or "")[:500],
        }
        # Broadcast to all WS connections on this session
        for sender in list(session.senders.values()):
            try:
                import orjson

                await sender(  # type: ignore[operator]
                    orjson.dumps(
                        {
                            "jsonrpc": "2.0",
                            "method": "session/update",
                            "params": {"updates": [notification]},
                        }
                    ).decode()
                )
            except Exception:
                pass  # best-effort

    async def _deliver_gateway(self, target: str, message: str) -> None:
        """Send via GatewayManager to an external platform channel."""
        if self._gateway_manager is None:
            logger.debug("No GatewayManager — skipping gateway delivery")
            return

        # Parse "gateway:<adapter>:<channel>"
        parts = target.split(":", 2)
        if len(parts) < 3:
            logger.warning("Malformed gateway target: %s", target)
            return

        adapter_name = parts[1]
        channel_id = parts[2]

        await self._retry_transient(
            lambda: self._gateway_manager.send_to_channel(adapter_name, channel_id, message)
        )

    # ------------------------------------------------------------------
    # Retry + cache
    # ------------------------------------------------------------------

    async def _retry_transient(
        self,
        fn: Any,
        *,
        delays: list[float] | None = None,
    ) -> Any:
        """Retry on transient delivery errors (5s/10s/20s)."""
        if delays is None:
            delays = list(DELIVERY_RETRY_DELAYS_S)
        last_exc: Exception | None = None
        for attempt in range(len(delays) + 1):
            try:
                return await fn()
            except Exception as exc:
                last_exc = exc
                if attempt < len(delays) and is_transient_delivery_error(str(exc)):
                    await asyncio.sleep(delays[attempt])
                else:
                    raise
        if last_exc:
            raise last_exc

    def _prune_cache(self) -> None:
        """Evict stale idempotency entries (>24h or >2000)."""
        now = time.time()
        to_delete = [k for k, (ts, _) in self._delivered.items() if now - ts > _IDEMPOTENCY_TTL_S]
        for k in to_delete:
            del self._delivered[k]
        # Hard cap
        if len(self._delivered) > _IDEMPOTENCY_MAX_ENTRIES:
            sorted_keys = sorted(self._delivered, key=lambda k: self._delivered[k][0])
            for k in sorted_keys[: len(self._delivered) - _IDEMPOTENCY_MAX_ENTRIES]:
                del self._delivered[k]

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_message(task: CronTask, execution: CronExecution) -> str:
        """Format execution result for delivery."""
        status_emoji = {
            "completed": "✅",
            "failed": "❌",
            "timeout": "⏰",
        }.get(execution.status, "❓")

        lines = [
            f"{status_emoji} Cron job **{task.id}** ({task.description or 'unnamed'})",
            f"Status: {execution.status}",
        ]
        if execution.duration_ms is not None:
            lines.append(f"Duration: {execution.duration_ms / 1000:.1f}s")
        if execution.error:
            lines.append(f"Error: {execution.error[:500]}")
        if execution.summary:
            lines.append(f"Result: {execution.summary[:1000]}")
        return "\n".join(lines)
