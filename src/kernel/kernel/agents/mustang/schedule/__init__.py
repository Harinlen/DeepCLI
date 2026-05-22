"""ScheduleManager — cron scheduling subsystem.

Design reference: ``docs/plans/schedule-manager.md``.

Independent Subsystem at the tail of the kernel startup DAG (after
SessionManager + GatewayManager).  Owns CronStore, CronScheduler,
CronExecutor, and DeliveryRouter.

Public surface: ``ScheduleManager`` is the only export.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, Field

from kernel.agents.mustang.schedule.executor import CronExecutor
from kernel.agents.mustang.schedule.schedule_parser import (
    compute_next_fire,
    human_schedule,
    parse_schedule,
)
from kernel.agents.mustang.schedule.scheduler import CronScheduler
from kernel.agents.mustang.schedule.store import CronStore
from kernel.agents.mustang.schedule.types import (
    CronExecution,
    CronTask,
    CronTaskStatus,
    ScheduleKind,
)
from kernel.core.lifecycle import Subsystem

if TYPE_CHECKING:
    from kernel.agents.mustang.module_table import KernelModuleTable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Flag / config schemas
# ---------------------------------------------------------------------------


class ScheduleFlags(BaseModel):
    """Feature flags for ScheduleManager (runtime-immutable)."""

    enabled: bool = Field(True, description="Enable schedule subsystem")


class ScheduleConfig(BaseModel):
    """Runtime configuration for ScheduleManager."""

    max_jobs: int = Field(default=50, ge=1, le=10000)
    max_concurrent_executions: int = Field(default=1, ge=1, le=20)
    default_max_age_days: int = Field(default=7, ge=0)
    default_timeout_minutes: int = Field(default=30, ge=1)
    default_inactivity_timeout_minutes: int = Field(default=10, ge=0)
    execution_retention_days: int = Field(default=30, ge=0)
    session_retention_hours: int = Field(default=24, ge=0)
    startup_stagger_seconds: float = Field(default=5, ge=0)


# ---------------------------------------------------------------------------
# ScheduleManager
# ---------------------------------------------------------------------------


class ScheduleManager(Subsystem):
    """Cron scheduling subsystem.

    Depends on: ConfigManager, SessionManager, GatewayManager (optional).
    Depended on by: nothing (leaf node in the DAG).
    """

    def __init__(self, module_table: KernelModuleTable) -> None:
        super().__init__(module_table)
        self._store = CronStore()
        self._scheduler: CronScheduler | None = None
        self._executor: CronExecutor | None = None
        self._flags: ScheduleFlags | None = None
        self._config: ScheduleConfig = ScheduleConfig()

    async def startup(self) -> None:
        """Initialize store, executor, scheduler; handle missed tasks."""
        from kernel.agents.mustang.sessions import SessionManager

        # Flags (optional — subsystem may not register its own section)
        try:
            self._flags = self._module_table.flags.register("schedule", ScheduleFlags)
        except ValueError:
            # Already registered (e.g. by tests) — read existing
            self._flags = self._module_table.flags.get_section("schedule")  # type: ignore[assignment]

        # Store
        state_dir = self._module_table.state_dir
        resource_home = state_dir.parent if state_dir.name == "state" else state_dir
        await self._store.startup_resource(resource_home)

        # Delivery
        session_mgr = cast(Any, self._module_table.get(SessionManager))  # type: ignore[type-abstract]
        gateway_mgr = None
        try:
            from kernel.agents.mustang.gateways import GatewayManager

            gateway_mgr = self._module_table.get(GatewayManager)
        except (KeyError, ImportError):
            pass
        from kernel.agents.mustang.schedule.delivery import DeliveryRouter

        self._delivery = DeliveryRouter(
            session_manager=session_mgr,
            gateway_manager=gateway_mgr,
        )

        # Hooks (optional)
        hooks_mgr = None
        try:
            from kernel.agents.mustang.hooks import HookManager

            hooks_mgr = self._module_table.get(HookManager)
        except (KeyError, ImportError):
            pass

        # Executor
        self._executor = CronExecutor(
            session_manager=session_mgr,
            delivery_router=self._delivery,
            hooks=hooks_mgr,
        )

        # Config
        try:
            cfg_section = self._module_table.config.get_section(
                file="schedule",
                section="defaults",
                schema=ScheduleConfig,
            )
            self._config = cfg_section.get()
        except Exception:
            self._config = ScheduleConfig()  # fallback to defaults

        # Scheduler
        self._scheduler = CronScheduler(
            store=self._store,
            executor=self._executor,
            max_concurrent=self._config.max_concurrent_executions,
            session_retention_hours=self._config.session_retention_hours,
            execution_retention_days=self._config.execution_retention_days,
        )
        # Wire heartbeat callback
        self._executor._heartbeat_fn = self._scheduler._heartbeat_fn_for_executor()

        # Start scheduler loop
        await self._scheduler.start()

        # Handle missed tasks from before this kernel started
        await self._scheduler.handle_startup_catchup()

        logger.info("ScheduleManager started")

    async def shutdown(self) -> None:
        """Stop scheduler and close store."""
        if self._scheduler:
            await self._scheduler.stop()
        await self._store.shutdown()
        logger.info("ScheduleManager shut down")

    # ------------------------------------------------------------------
    # Public API (consumed by tools and commands)
    # ------------------------------------------------------------------

    async def create_task(
        self,
        *,
        schedule_expr: str,
        prompt: str,
        description: str = "",
        recurring: bool | None = None,
        durable: bool = True,
        skills: list[str] | None = None,
        model: str | None = None,
        delivery: str = "session,acp",
        repeat_count: int | None = None,
        repeat_duration_seconds: float | None = None,
        repeat_until: float | None = None,
        session_id: str | None = None,
        project_dir: str | None = None,
        owner_agent_id: str = "primary",
    ) -> CronTask:
        """Parse a schedule expression and create a new cron task.

        Returns the created task with ``id`` and ``next_fire_at`` set.

        Raises:
            ValueError: If the schedule expression is invalid or
                max_jobs limit is reached.
        """
        # Check job limit
        active = await self._store.list_active()
        if len(active) >= self._config.max_jobs:
            raise ValueError("Maximum number of cron jobs reached")

        # Parse schedule
        schedule = parse_schedule(schedule_expr)

        # Auto-infer recurring if not specified
        if recurring is None:
            recurring = schedule.kind in (ScheduleKind.cron, ScheduleKind.every)

        # Validate: at + recurring=True is nonsensical
        if schedule.kind == ScheduleKind.at and recurring:
            raise ValueError(
                "Cannot use recurring=True with a one-time schedule (at/delay). "
                "Use a cron or interval schedule for recurring tasks."
            )

        from kernel.agents.mustang.schedule.types import DeliveryConfig, RepeatConfig

        task = CronTask(
            id=uuid.uuid4().hex[:8],
            owner_agent_id=owner_agent_id,
            schedule=schedule,
            prompt=prompt,
            description=description,
            recurring=recurring,
            durable=durable,
            skills=skills or [],
            model=model,
            delivery=DeliveryConfig(target=delivery),
            session_id=session_id,
            project_dir=project_dir,
            created_at=time.time(),
            repeat=RepeatConfig(
                max_count=repeat_count,
                max_duration_seconds=repeat_duration_seconds,
                until=repeat_until,
            ),
        )

        # Compute initial next_fire_at
        if schedule.kind == ScheduleKind.at:
            task.next_fire_at = schedule.run_at
        else:
            task.next_fire_at = compute_next_fire(schedule)

        await self._store.add(task)

        # Wake the scheduler so it picks up the new task
        if self._scheduler:
            await self._scheduler.notify_change()

        logger.info(
            "Created cron task %s: %s (%s)",
            task.id,
            human_schedule(schedule),
            "recurring" if recurring else "one-shot",
        )
        return task

    async def delete_task(self, task_id: str) -> bool:
        """Soft-delete a cron task."""
        ok = await self._store.remove(task_id)
        if ok and self._scheduler:
            await self._scheduler.notify_change()
        return ok

    async def list_tasks(self, *, include_completed: bool = False) -> list[CronTask]:
        """List cron tasks."""
        if include_completed:
            return await self._store.list_all()
        return await self._store.list_active()

    async def get_task(self, task_id: str) -> CronTask | None:
        """Get a single task by ID."""
        return await self._store.get(task_id)

    async def pause_task(self, task_id: str) -> bool:
        """Pause a task (stops scheduling)."""
        task = await self._store.get(task_id)
        if not task or task.status != CronTaskStatus.active:
            return False
        await self._store.update_status(task_id, CronTaskStatus.paused, next_fire_at=None)
        if self._scheduler:
            await self._scheduler.notify_change()
        return True

    async def resume_task(self, task_id: str) -> bool:
        """Resume a paused task."""
        task = await self._store.get(task_id)
        if not task or task.status != CronTaskStatus.paused:
            return False
        next_at = compute_next_fire(task.schedule)
        await self._store.update_status(
            task_id,
            CronTaskStatus.active,
            next_fire_at=next_at,
            consecutive_failures=0,
        )
        if self._scheduler:
            await self._scheduler.notify_change()
        return True

    async def trigger_now(self, task_id: str) -> CronExecution | None:
        """Immediately fire a task (like ``hermes cron run``)."""
        task = await self._store.get(task_id)
        if not task:
            return None
        if not self._executor:
            return None
        execution = await self._executor.execute(task)
        await self._store.add_execution(execution)
        return execution

    async def list_executions(self, task_id: str, limit: int = 20) -> list[CronExecution]:
        """List recent executions for a task."""
        return await self._store.list_executions(task_id, limit)
