"""REPL — scriptable Python worker for dense tool orchestration."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.context import ToolContext
from kernel.tools.repl import ReplRunner
from kernel.tools.tool import RiskContext, Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)


class ReplTool(Tool[dict[str, Any], dict[str, Any]]):
    """Execute model-authored Python in a per-session worker process."""

    name = "REPL"
    description_key = "tools/repl"
    description = "Execute Python REPL code with access to DeepCLI tools as async helpers."
    kind = ToolKind.execute
    should_defer = False
    always_load = True
    cache = True
    max_result_size_chars = 200_000
    interrupt_behavior = "cancel"

    input_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source code to execute in the session REPL worker.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Kill the REPL worker after this many ms. Default 60000.",
            },
        },
        "required": ["code"],
    }

    def __init__(self) -> None:
        self._runner = ReplRunner()

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="medium",
            default_decision="ask",
            reason="REPL executes model-authored orchestration code",
        )

    async def validate_input(self, input: dict[str, Any], ctx: RiskContext) -> None:
        code = input.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ToolInputError("code must be a non-empty string")
        if len(code) > 64_000:
            raise ToolInputError("code exceeds 64,000 character limit")
        timeout_ms = input.get("timeout_ms")
        if timeout_ms is not None:
            if not isinstance(timeout_ms, int) or timeout_ms <= 0:
                raise ToolInputError("timeout_ms must be a positive integer")
            if timeout_ms > 600_000:
                raise ToolInputError("timeout_ms must be <= 600000")

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        if ctx.run_nested_tool is None:
            yield _repl_tool_result(
                stdout="",
                stderr="REPL nested tool dispatcher is unavailable",
                value=None,
                error="REPL nested tool dispatcher is unavailable",
                reset=False,
            )
            return

        timeout_ms = int(input.get("timeout_ms") or 60_000)
        result = await self._runner.run(
            session_id=ctx.session_id,
            cwd=ctx.cwd,
            code=input["code"],
            run_tool=ctx.run_nested_tool,
            timeout_ms=timeout_ms,
        )
        yield _repl_tool_result(
            stdout=result.stdout,
            stderr=result.stderr,
            value=result.value,
            error=result.error,
            reset=result.reset,
        )

    async def shutdown(self) -> None:
        await self._runner.shutdown_all()


def _repl_tool_result(
    *,
    stdout: str,
    stderr: str,
    value: Any,
    error: str | None,
    reset: bool,
) -> ToolCallResult:
    parts = ["<repl_result>"]
    if reset:
        parts.append("state: reset")
    if stdout:
        parts.append("stdout:")
        parts.append(stdout.rstrip())
    if stderr:
        parts.append("stderr:")
        parts.append(stderr.rstrip())
    if error:
        parts.append("error:")
        parts.append(error.rstrip())
    else:
        parts.append("return:")
        parts.append(repr(value))
    parts.append("</repl_result>")
    text = "\n".join(parts)
    return ToolCallResult(
        data={
            "stdout": stdout,
            "stderr": stderr,
            "value": value,
            "error": error,
            "reset": reset,
        },
        llm_content=[TextBlock(type="text", text=text)],
        display=TextDisplay(text=text, language="python"),
    )


__all__ = ["ReplTool"]
