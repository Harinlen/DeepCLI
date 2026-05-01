"""In-memory registry of every live kernel module.

The table is the kernel's equivalent of Linux's module list: one
authoritative place that records *what is currently loaded into the
kernel*.  It is built once at startup by the FastAPI lifespan and
exposed on ``app.state.module_table`` so routes and handlers can look
things up from a single entry point.

The two bootstrap services (``FlagManager`` / ``ConfigManager``) and
the regular ``Subsystem`` subclasses live side-by-side in the table
but are stored differently on purpose:

- **Bootstrap services** occupy dedicated typed attributes
  (``flags`` / ``config``).  They are instantiated before any regular
  Subsystem, every Subsystem depends on them, and they have richer
  public APIs than the uniform startup/shutdown contract — giving
  them their own fields makes that asymmetry visible in the type
  system and keeps lookup cheap and typed.

- **Regular Subsystems** go into ``_subsystems``, a dict keyed by
  the Subsystem class itself so :meth:`get` can return a strongly-
  typed instance without string lookups or ``cast`` at call sites.
  Insertion order is preserved so the lifespan can unload in reverse
  without keeping a parallel list.

The table does not manage lifecycles — it only remembers what was
loaded.  The lifespan is responsible for constructing, starting,
registering, and (on shutdown) unloading entries.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from kernel.config import ConfigManager
    from kernel.flags import FlagManager
    from kernel.prompts import PromptManager
    from kernel.secrets import SecretManager
    from kernel.subsystem import Subsystem

T = TypeVar("T", bound="Subsystem")


class KernelModuleTable:
    """Registry of live kernel modules — bootstrap services + subsystems.

    ``state_dir`` is the kernel-wide location for subsystem runtime
    artifacts (auth tokens, memory indices, session metadata, ...).
    It is kept here rather than on any individual subsystem because
    several subsystems write into the same tree and tests need to
    redirect it in one place.  Callers must treat the path as
    authoritative — subsystems must not fall back to
    ``~/.mustang/state`` on their own.
    """

    def __init__(
        self,
        flags: FlagManager,
        config: ConfigManager,
        state_dir: Path,
        secrets: SecretManager | None = None,
        prompts: PromptManager | None = None,
    ) -> None:
        self.flags: FlagManager = flags
        self.config: ConfigManager = config
        self.state_dir: Path = state_dir
        self.secrets: SecretManager | None = secrets
        self.prompts: PromptManager | None = prompts
        self.agent_hub: Any | None = None
        self.agent_hub_endpoint: str | None = None
        self._subsystems: dict[type[Subsystem], Subsystem] = {}

    def register(self, subsystem: Subsystem) -> None:
        """Record a successfully-started subsystem in the table.

        Called by the lifespan after ``Subsystem.load`` returns a live
        instance.  A subsystem may appear at most once — loading the
        same class twice is a programming error, not a degraded-mode
        path, so this raises rather than silently overwriting.
        """
        cls = type(subsystem)
        if cls in self._subsystems:
            raise ValueError(f"Subsystem already registered: {cls.__name__}")
        self._subsystems[cls] = subsystem

    def get(self, cls: type[T]) -> T:
        """Return the live instance of ``cls``.

        Keyed by class so the return type is inferred automatically —
        ``module_table.get(ConnectionAuthenticator)`` yields a
        ``ConnectionAuthenticator`` without any ``cast`` at the call site.  Raises ``KeyError`` if
        the subsystem was skipped (disabled via flags) or failed to
        load (degraded mode); callers that want to tolerate absence
        should use :meth:`has` first.
        """
        try:
            return cast(T, self._subsystems[cls])
        except KeyError as exc:
            raise KeyError(f"Subsystem not loaded: {cls.__name__}") from exc

    def has(self, cls: type[Subsystem]) -> bool:
        """Return ``True`` if ``cls`` was loaded successfully."""
        return cls in self._subsystems

    def subsystems(self) -> list[Subsystem]:
        """Return loaded subsystems in load order.

        Used by the lifespan to unload in reverse; iteration order is
        guaranteed by ``dict`` insertion order.
        """
        return list(self._subsystems.values())
