"""Config subsystem — layered runtime configuration.

ConfigManager is a **bootstrap service**, not a
:class:`kernel.core.lifecycle.Subsystem` subclass.  Its public API
(``bind_section`` / ``get_section`` / signal-based change
notifications) is richer than the uniform ``startup`` / ``shutdown``
contract, and every regular subsystem depends on it being already
running.  The kernel lifespan constructs it explicitly, right after
:class:`kernel.core.flags.FlagManager`, and treats its failure as fatal to
kernel boot.

There is no ``shutdown`` step: section updates persist synchronously
through :meth:`kernel.core.config.section._Section.update`, so no state is
left in memory at kernel exit.
"""

from __future__ import annotations

from kernel.core.config.manager import ConfigManager
from kernel.core.config.section import MutableSection, ReadOnlySection

__all__ = ["ConfigManager", "MutableSection", "ReadOnlySection"]
