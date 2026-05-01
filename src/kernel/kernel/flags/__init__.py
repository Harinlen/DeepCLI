"""Feature flag subsystem.

FlagManager is the earliest-loaded bootstrap service in the kernel.
It owns ``~/.mustang/flags.yaml`` and hands out strongly-typed,
runtime-frozen Pydantic instances for each registered section.
Runtime-mutable configuration lives in ``kernel.config`` instead.
"""

from __future__ import annotations

from kernel.flags.kernel_flags import KernelFlags
from kernel.flags.manager import FlagManager

__all__ = ["FlagManager", "KernelFlags"]
