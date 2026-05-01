"""Base class for regular kernel subsystems managed by the lifespan.

Regular subsystems are created, started, and stopped by the kernel
lifespan in a deterministic order (see ``kernel/app.py``).  They
implement two async lifecycle hooks and inherit from
:class:`Subsystem` so that the lifespan can treat every manager
uniformly without sprinkling structural type checks around.

Every subsystem is handed the :class:`KernelModuleTable` at
construction time — that is the *only* channel for reaching
``FlagManager`` / ``ConfigManager`` and other subsystems.  The table
replaces ad-hoc module-level singletons and keeps the dependency
graph explicit: if ``startup()`` touches ``self._module_table.flags``
you can grep for it.

The base class also owns the error-handling contract every regular
subsystem shares — instantiation + ``startup()`` inside a try/except
and ``shutdown()`` tolerating partial state — so the lifespan stays
thin and none of the managers duplicate that boilerplate.

``FlagManager`` and ``ConfigManager`` are **not** Subsystem
subclasses: they are bootstrap services with richer public APIs
(``register`` / ``bind_section`` / signal-based notifications) and
every regular subsystem depends on them already being up.  The
lifespan constructs them explicitly before building the module
table, and their failures are always fatal to kernel boot.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kernel.module_table import KernelModuleTable

logger = logging.getLogger(__name__)


class Subsystem(ABC):
    """Lifecycle contract shared by every kernel manager.

    Contract
    --------
    - ``startup`` runs once, before the subsystem serves any request.
      It is the only place where the subsystem is allowed to acquire
      external resources (files, sockets, background tasks, etc.) or
      register itself with ``FlagManager`` / ``ConfigManager``.

    - ``shutdown`` runs once, after the subsystem has stopped serving
      requests.  It is the inverse of ``startup``: release resources,
      drain background work, persist state.  It must be tolerant of
      a failed or partial startup — the kernel still calls it so that
      other subsystems are not starved of cleanup.

    Neither hook should raise for "already started" / "already
    stopped"; the lifespan calls each exactly once and logs any
    exception without aborting the other subsystems.

    The canonical entry points used by the lifespan are
    :meth:`load` and :meth:`unload`, not ``startup`` and ``shutdown``
    directly — those wrap the raw hooks with the uniform
    error-handling policy.  Subclasses override ``startup`` /
    ``shutdown`` and never call ``load`` / ``unload`` themselves.

    Subclasses that need their own ``__init__`` **must** accept
    ``module_table`` and forward it via ``super().__init__``.  Any
    state set on ``self`` before ``super().__init__`` will not see
    ``self._module_table`` — keep constructors thin and do real
    initialization in :meth:`startup`.
    """

    _lifecycle_name: str = ""

    def __init__(self, module_table: KernelModuleTable) -> None:
        self._module_table = module_table

    @abstractmethod
    async def startup(self) -> None:
        """Acquire resources and make the subsystem ready to serve."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Release resources acquired during :meth:`startup`."""

    @classmethod
    async def load(cls, name: str, module_table: KernelModuleTable) -> Subsystem | None:
        """Instantiate the subsystem and run :meth:`startup`.

        Analogous to loading a Linux kernel module: the factory is
        constructed against the shared :class:`KernelModuleTable`,
        registered under ``name``, and asked to bring itself up.  The
        ``name`` is stored on the instance so :meth:`unload` can log
        under the same identifier without the caller tracking a
        parallel mapping.

        On failure the exception is logged and ``None`` is returned,
        so the caller can skip the subsystem and continue in degraded
        mode.  Regular subsystems are never required — the only
        fatal-on-failure services are the bootstrap ones (Flag /
        Config), which don't use this path.
        """
        instance = cls(module_table)
        instance._lifecycle_name = name
        try:
            await instance.startup()
        except Exception:
            logger.exception("Subsystem %s failed to load — degraded mode", name)
            return None
        return instance

    async def unload(self) -> None:
        """Run :meth:`shutdown`, logging failures but never re-raising.

        Inverse of :meth:`load`.  The lifespan must keep draining
        other subsystems even when one of them dies during cleanup,
        so this method swallows exceptions after logging them.
        """
        try:
            await self.shutdown()
        except Exception:
            logger.exception(
                "Subsystem %s failed to unload cleanly",
                self._lifecycle_name or type(self).__name__,
            )
