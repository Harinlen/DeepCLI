"""Feature flag subsystem.

FlagManager is the earliest-loaded bootstrap service in the kernel.
It owns ``~/.deepcli/config/flags.yaml`` and hands out strongly-typed,
runtime-frozen Pydantic instances for each registered section.
Runtime-mutable configuration lives in ``kernel.core.config`` instead.
"""

from __future__ import annotations

from kernel.core.flags.kernel_flags import KernelFlags
from kernel.core.flags.manager import FlagManager

__all__ = ["FlagManager", "KernelFlags"]
