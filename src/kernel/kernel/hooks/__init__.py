"""Hooks subsystem — in-process Python hook engine.

See ``docs/plans/landed/hook-manager.md`` for the full design.

Public surface:

- :class:`HookManager` — Subsystem loaded at step 7 of the kernel
  lifespan; owns the :class:`HookRegistry` and exposes :meth:`fire`.
- :class:`HookEvent` — the 13-event enum that every fire-site keys on.
- :class:`HookBlock` — exception handlers raise to veto the current
  event (only on events whose :class:`HookEventSpec` says ``can_block``).
- :class:`AmbientContext` — frozen ambient state (session_id / cwd /
  agent_depth / mode / timestamp) every hook can see.
- :class:`HookEventCtx` — mutable per-fire payload; handlers modify
  ``tool_input`` / ``user_text`` / ``messages`` directly.
- :data:`EVENT_SPECS` — per-event ``can_block`` /
  ``accepts_input_mutation`` table.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from kernel.hooks.config import HooksConfig, ProjectHooksConfig
from kernel.hooks.loader import LoadedHook, discover
from kernel.hooks.manifest import HookManifest, HookRequires, ManifestError
from kernel.hooks.registry import HookRegistry
from kernel.hooks.types import (
    EVENT_SPECS,
    AmbientContext,
    HookBlock,
    HookEvent,
    HookEventCtx,
    HookEventSpec,
    HookHandler,
)
from kernel.subsystem import Subsystem

if TYPE_CHECKING:
    from kernel.module_table import KernelModuleTable

logger = logging.getLogger(__name__)


# Default discovery roots.  Overridable in tests via ``HookManager``
# constructor params; production always uses these via ``Subsystem.load``.
_DEFAULT_USER_HOOKS_DIR = Path.home() / ".mustang" / "hooks"
_DEFAULT_PROJECT_HOOKS_SUBDIR = Path(".mustang") / "hooks"


class HookManager(Subsystem):
    """In-process hook engine.

    Stateless beyond the registry: no per-session state, no background
    tasks, no timers.  All mutable session-scoped state belongs in
    :class:`SessionManager` (e.g. ``Session.queue_reminders`` for the
    drained ``ctx.messages`` list).

    Trust model: hooks are **trusted local code**.  ``handler.py`` is
    imported into the kernel process; a buggy hook that raises is
    logged and skipped, but a hook that runs an infinite loop will
    hang the daemon — same trust boundary as the Tools subsystem.
    """

    def __init__(
        self,
        module_table: KernelModuleTable,
        *,
        user_hooks_dir: Path | None = None,
        project_hooks_dir: Path | None = None,
    ) -> None:
        """Construct.

        ``user_hooks_dir`` and ``project_hooks_dir`` exist for test
        injection only.  Production loads hit the defaults
        (``~/.mustang/hooks`` and ``<cwd>/.mustang/hooks``) because
        :meth:`Subsystem.load` constructs with just ``module_table``.
        """
        super().__init__(module_table)
        self._user_hooks_dir: Path = (
            user_hooks_dir if user_hooks_dir is not None else _DEFAULT_USER_HOOKS_DIR
        )
        self._project_hooks_dir: Path = (
            project_hooks_dir
            if project_hooks_dir is not None
            else Path.cwd() / _DEFAULT_PROJECT_HOOKS_SUBDIR
        )
        self._registry = HookRegistry()
        self._loaded: list[LoadedHook] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Bind the ``hooks`` config section, discover and import hooks.

        Empty / missing user_hooks_dir is the common case (no hooks
        configured) and produces a no-op startup with an empty registry.
        """
        section = self._module_table.config.bind_section(
            file="hooks", section="hooks", schema=HooksConfig
        )
        cfg = section.get()

        self._loaded = discover(
            user_dir=self._user_hooks_dir,
            project_dir=self._project_hooks_dir,
            project_enabled=cfg.project_hooks.enabled,
        )
        for entry in self._loaded:
            for event in entry.events:
                self._registry.register(event, entry.handler)

        logger.info(
            "HookManager started: %d handler-registration(s) across %d hook(s)",
            len(self._registry),
            len(self._loaded),
        )

    async def shutdown(self) -> None:
        """Drop the registry.  No background work to drain."""
        self._registry.clear()
        self._loaded = []
        logger.info("HookManager: shutdown complete")

    # ------------------------------------------------------------------
    # Primary API — fire
    # ------------------------------------------------------------------

    async def fire(self, ctx: HookEventCtx) -> bool:
        """Run all handlers for ``ctx.event`` in registration order.

        Returns ``True`` when a handler raised :class:`HookBlock` **and**
        the event accepts blocking (``EVENT_SPECS[event].can_block``).
        ``HookBlock`` raised on a non-blocking event is logged and
        ignored — main flow continues.

        Side-effect contract:

        - Handlers may mutate ``ctx.tool_input`` / ``ctx.user_text``
          (callers honour the rewrite when the event spec allows).
        - Handlers may ``ctx.messages.append(...)`` to schedule
          system-reminder strings; the caller drains and queues into
          the Session after this returns.
        - Handlers raising plain ``Exception`` are caught, logged with
          traceback, and the next handler still runs (fail-open).

        After ``fire`` returns, ``ctx`` is logically owned by the
        caller's drain path — do not continue mutating ``ctx.messages``
        once it has been handed to ``session.queue_reminders``.
        """
        spec = EVENT_SPECS[ctx.event]
        for handler in self._registry.get(ctx.event):
            try:
                result = handler(ctx)
                if asyncio.iscoroutine(result):
                    await result
            except HookBlock as block:
                if spec.can_block:
                    logger.info("hook blocked %s: %s", ctx.event.value, block.reason)
                    return True
                logger.warning(
                    "HookBlock raised on non-blocking event %s, ignoring",
                    ctx.event.value,
                )
            except Exception:
                logger.exception("hook crashed on %s — fail-open", ctx.event.value)
        return False

    # ------------------------------------------------------------------
    # Introspection (used by /hooks list-style commands and debug tools)
    # ------------------------------------------------------------------

    def loaded_hooks(self) -> tuple[LoadedHook, ...]:
        """Return immutable snapshot of every hook that survived discovery."""
        return tuple(self._loaded)


__all__ = [
    "AmbientContext",
    "EVENT_SPECS",
    "HookBlock",
    "HookEvent",
    "HookEventCtx",
    "HookEventSpec",
    "HookHandler",
    "HookManager",
    "HookManifest",
    "HookRequires",
    "HooksConfig",
    "LoadedHook",
    "ManifestError",
    "ProjectHooksConfig",
]
