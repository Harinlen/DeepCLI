"""Compatibility exports for Orchestrator tool execution."""

from __future__ import annotations

from kernel.orchestrator.tools_exec.executor import ToolExecutor
from kernel.orchestrator.tools_exec.partition import partition_tool_calls
from kernel.orchestrator.tools_exec.permissions import (
    permission_options_from_suggestions as _permission_options_from_suggestions,
)
from kernel.orchestrator.tools_exec.result_mapping import (
    apply_result_budget as _apply_result_budget,
)
from kernel.orchestrator.tools_exec.result_mapping import coerce_content as _coerce_content

__all__ = [
    "ToolExecutor",
    "partition_tool_calls",
    "_permission_options_from_suggestions",
    "_coerce_content",
    "_apply_result_budget",
]
