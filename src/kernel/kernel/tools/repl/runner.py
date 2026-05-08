"""Parent-side manager for per-session REPL worker processes."""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import queue
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.tools.repl.ipc import ReplRunResult
from kernel.tools.repl.worker_main import worker_main
from kernel.tools.types import NestedToolResult


@dataclass
class _WorkerHandle:
    process: Any
    in_q: mp.Queue
    out_q: mp.Queue
    lock: asyncio.Lock


class ReplRunner:
    """Own REPL worker processes and bridge their tool requests to Kernel."""

    def __init__(self) -> None:
        self._workers: dict[str, _WorkerHandle] = {}
        self._mp_context = mp.get_context("spawn")

    async def run(
        self,
        *,
        session_id: str,
        cwd: Path,
        code: str,
        run_tool: Any,
        timeout_ms: int,
    ) -> ReplRunResult:
        worker, reset = self._get_worker(session_id, cwd)
        async with worker.lock:
            request_id = id(code)
            worker.in_q.put({"type": "execute", "id": request_id, "code": code})
            try:
                return await asyncio.wait_for(
                    self._serve_until_result(worker, request_id, run_tool, reset),
                    timeout=timeout_ms / 1000.0,
                )
            except asyncio.TimeoutError:
                self.shutdown(session_id)
                return ReplRunResult(
                    stdout="",
                    stderr=f"REPL execution timed out after {timeout_ms}ms",
                    error=f"REPL execution timed out after {timeout_ms}ms",
                    reset=True,
                )
            except asyncio.CancelledError:
                self.shutdown(session_id)
                raise

    def shutdown(self, session_id: str) -> None:
        worker = self._workers.pop(session_id, None)
        if worker is None:
            return
        try:
            worker.in_q.put({"type": "shutdown"})
        except Exception:
            pass
        worker.process.join(timeout=1)
        if worker.process.is_alive():
            worker.process.terminate()
            worker.process.join(timeout=2)
        if worker.process.is_alive():
            worker.process.kill()
            worker.process.join(timeout=2)
        _close_queue(worker.in_q)
        _close_queue(worker.out_q)

    async def shutdown_all(self) -> None:
        for session_id in list(self._workers):
            self.shutdown(session_id)

    def _get_worker(self, session_id: str, cwd: Path) -> tuple[_WorkerHandle, bool]:
        worker = self._workers.get(session_id)
        if worker is not None and worker.process.is_alive():
            return worker, False
        in_q: mp.Queue = self._mp_context.Queue()
        out_q: mp.Queue = self._mp_context.Queue()
        process = self._mp_context.Process(
            target=worker_main,
            args=(in_q, out_q, str(cwd)),
            daemon=True,
        )
        process.start()
        worker = _WorkerHandle(process=process, in_q=in_q, out_q=out_q, lock=asyncio.Lock())
        self._workers[session_id] = worker
        return worker, True

    async def _serve_until_result(
        self,
        worker: _WorkerHandle,
        request_id: int,
        run_tool: Any,
        reset: bool,
    ) -> ReplRunResult:
        while True:
            msg = await asyncio.to_thread(_queue_get_poll, worker.out_q)
            if msg is None:
                await asyncio.sleep(0)
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "tool_call":
                result = await self._run_nested(msg, run_tool)
                worker.in_q.put(
                    {
                        "type": "tool_result",
                        "id": msg.get("id"),
                        "ok": not result.is_error,
                        "text": result.text,
                        "data": result.data,
                    }
                )
                continue
            if msg.get("type") == "execute_result" and msg.get("id") == request_id:
                return ReplRunResult(
                    stdout=str(msg.get("stdout") or ""),
                    stderr=str(msg.get("stderr") or ""),
                    value=msg.get("value"),
                    error=None if msg.get("ok") else str(msg.get("error") or "REPL failed"),
                    reset=reset,
                )

    @staticmethod
    async def _run_nested(msg: dict[str, Any], run_tool: Any) -> NestedToolResult:
        try:
            tool_input = dict(msg.get("input") or {})
            if isinstance(msg.get("cwd"), str):
                tool_input["__repl_cwd"] = msg["cwd"]
            result = await run_tool(str(msg.get("tool") or ""), tool_input)
            if isinstance(result, NestedToolResult):
                return result
            return NestedToolResult(tool_name=str(msg.get("tool") or ""), text=str(result))
        except Exception as exc:
            return NestedToolResult(
                tool_name=str(msg.get("tool") or ""),
                text=str(exc),
                is_error=True,
            )


def _close_queue(queue: mp.Queue) -> None:
    try:
        queue.close()
    except Exception:
        pass
    try:
        queue.join_thread()
    except Exception:
        pass


def _queue_get_poll(queue_obj: mp.Queue) -> Any | None:
    try:
        return queue_obj.get(timeout=0.1)
    except queue.Empty:
        return None


__all__ = ["ReplRunner", "ReplRunResult"]
