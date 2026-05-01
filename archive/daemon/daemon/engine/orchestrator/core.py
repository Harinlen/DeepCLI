"""Public :class:`Orchestrator` — re-export from the composition module.

Previously this module assembled the Orchestrator via multi-inheritance
of 6 mixins.  After the composition refactor, the Orchestrator is a
single class in :mod:`orchestrator.py`.  This module re-exports it
so existing ``from daemon.engine.orchestrator.core import Orchestrator``
imports keep working.
"""

from __future__ import annotations

from daemon.engine.orchestrator.orchestrator import Orchestrator

__all__ = ["Orchestrator"]
