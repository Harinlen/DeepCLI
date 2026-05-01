"""Backward-compatible re-export of ResultStore.

The implementation moved to :mod:`daemon.extensions.tools.result_store`
in Phase 4.3.  This module preserves the old import path and the
``McpResultStore`` alias so existing code (including tests) continues
to work without changes.
"""

from daemon.extensions.tools.result_store import ResultStore

# Backward-compatible alias
McpResultStore = ResultStore

__all__ = ["McpResultStore", "ResultStore"]
