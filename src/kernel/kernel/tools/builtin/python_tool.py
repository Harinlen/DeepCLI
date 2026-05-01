"""Python — per-session user Python runtime for kernel-side REPL execution."""

from __future__ import annotations

import asyncio
import contextlib
import io
import multiprocessing as mp
import traceback
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.context import ToolContext
from kernel.tools.tool import RiskContext, Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)


@dataclass
class _Worker:
    process: mp.Process
    requests: mp.Queue
    responses: mp.Queue
    lock: asyncio.Lock


_WORKERS: dict[str, _Worker] = {}


class PythonTool(Tool[dict[str, Any], str]):
    """Execute Python in a persistent per-session worker process."""

    name = "Python"
    description = "Execute Python code in this session's Python runtime."
    kind = ToolKind.execute
    interrupt_behavior = "cancel"

    input_schema = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python code to execute."},
            "timeout_ms": {
                "type": "integer",
                "description": "Terminate execution after this many ms. Default 120000.",
            },
        },
        "required": ["code"],
    }

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="medium",
            default_decision="ask",
            reason="user Python execution can mutate files or run subprocesses",
        )

    async def validate_input(self, input: dict[str, Any], ctx: RiskContext) -> None:
        code = input.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ToolInputError("code must be a non-empty string")
        if len(code) > 64_000:
            raise ToolInputError("code exceeds 64,000 character limit")

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        code = input["code"]
        timeout_ms = int(input.get("timeout_ms") or 120_000)
        worker = _get_worker(ctx.session_id, str(ctx.cwd))

        async with worker.lock:
            request_id = id(code)
            worker.requests.put({"id": request_id, "code": code})
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(_wait_for_response, worker.responses, request_id),
                    timeout=timeout_ms / 1000.0,
                )
            except asyncio.TimeoutError:
                shutdown_python_worker(ctx.session_id)
                error = f"python execution timed out after {timeout_ms}ms"
                yield _python_result(1, "", error)
                return
            except asyncio.CancelledError:
                shutdown_python_worker(ctx.session_id)
                raise

        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        if stdout:
            yield ToolCallProgress(content=[TextBlock(type="text", text=stdout)])
        if stderr:
            yield ToolCallProgress(content=[TextBlock(type="text", text=stderr)])
        exit_code = 0 if result.get("ok") else 1
        yield _python_result(exit_code, stdout, stderr)


def shutdown_python_worker(session_id: str) -> None:
    """Terminate and forget a session's Python worker."""
    worker = _WORKERS.pop(session_id, None)
    if worker is None:
        return
    if worker.process.is_alive():
        worker.process.terminate()
        worker.process.join(timeout=2)
    if worker.process.is_alive():
        worker.process.kill()
        worker.process.join(timeout=2)


def _get_worker(session_id: str, cwd: str) -> _Worker:
    worker = _WORKERS.get(session_id)
    if worker is not None and worker.process.is_alive():
        return worker
    requests: mp.Queue = mp.Queue()
    responses: mp.Queue = mp.Queue()
    process = mp.Process(target=_worker_main, args=(requests, responses, cwd), daemon=True)
    process.start()
    worker = _Worker(process=process, requests=requests, responses=responses, lock=asyncio.Lock())
    _WORKERS[session_id] = worker
    return worker


def _wait_for_response(responses: mp.Queue, request_id: int) -> dict[str, Any]:
    while True:
        result = responses.get(timeout=0.1)
        if result.get("id") == request_id:
            return result


def _worker_main(requests: mp.Queue, responses: mp.Queue, cwd: str) -> None:
    import os

    os.chdir(cwd)
    ns: dict[str, Any] = {"__name__": "__mustang_repl__"}
    while True:
        try:
            request = requests.get()
        except (EOFError, KeyboardInterrupt):
            return
        request_id = request["id"]
        code = request["code"]
        stdout = io.StringIO()
        stderr = io.StringIO()
        ok = True
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                compiled = _compile_user_code(code)
                if compiled[0] == "eval":
                    value = eval(compiled[1], ns, ns)  # noqa: S307 - intentional REPL execution
                    if value is not None:
                        print(repr(value))
                else:
                    exec(compiled[1], ns, ns)  # noqa: S102 - intentional REPL execution
            except Exception:
                ok = False
                traceback.print_exc(file=stderr)
        responses.put(
            {
                "id": request_id,
                "ok": ok,
                "stdout": stdout.getvalue(),
                "stderr": stderr.getvalue(),
            }
        )


def _compile_user_code(code: str) -> tuple[str, Any]:
    try:
        return "eval", compile(code, "<mustang-python>", "eval")
    except SyntaxError:
        return "exec", compile(code, "<mustang-python>", "exec")


def _python_result(exit_code: int, stdout: str, stderr: str) -> ToolCallResult:
    body_parts = []
    if stdout:
        body_parts.append(stdout.rstrip())
    if stderr:
        body_parts.append(stderr.rstrip())
    if exit_code != 0:
        body_parts.append(f"[exit {exit_code}]")
    body = "\n".join(body_parts) if body_parts else "(no output)"
    return ToolCallResult(
        data={"exit_code": exit_code, "stdout": stdout, "stderr": stderr},
        llm_content=[TextBlock(type="text", text=body)],
        display=TextDisplay(text=body, language="python"),
    )


__all__ = ["PythonTool", "shutdown_python_worker"]
