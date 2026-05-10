"""Worker process entrypoint for scriptable REPL execution."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import io
import pickle
import sys
import traceback
import uuid
from collections.abc import Coroutine
from multiprocessing.queues import Queue
from typing import Any, cast

from kernel.agents.mustang.tools.repl.linter import lint_repl_code


def worker_main(in_q: Queue, out_q: Queue, cwd: str) -> None:
    """Run requests until the parent terminates the process."""
    import os

    try:
        os.chdir(cwd)
    except OSError:
        pass
    globals_: dict[str, Any] = _initial_globals(in_q, out_q)
    globals_["o"] = {}

    while True:
        try:
            request = in_q.get()
        except (EOFError, KeyboardInterrupt):
            return
        if not isinstance(request, dict):
            continue
        if request.get("type") == "shutdown":
            return
        if request.get("type") != "execute":
            continue
        request_id = request.get("id")
        code = str(request.get("code") or "")
        result = _execute(code, globals_)
        _safe_put(out_q, {"type": "execute_result", "id": request_id, **result})


def _initial_globals(in_q: Queue, out_q: Queue) -> dict[str, Any]:
    async def _tool(tool_name: str, **tool_input: Any) -> Any:
        return await _call_parent_tool(in_q, out_q, tool_name, tool_input)

    def _with_positional(input: dict[str, Any], **defaults: Any) -> dict[str, Any]:
        for key, value in defaults.items():
            if value is not None and key not in input:
                input[key] = value
        return input

    async def Read(path: str | None = None, **input: Any) -> Any:  # noqa: N802
        return await _tool("Read", **_with_positional(input, file_path=path))

    async def Write(  # noqa: N802
        path: str | None = None,
        content: str | None = None,
        **input: Any,
    ) -> Any:
        return await _tool("Write", **_with_positional(input, file_path=path, content=content))

    async def Edit(  # noqa: N802
        path: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
        **input: Any,
    ) -> Any:
        return await _tool(
            "Edit",
            **_with_positional(
                input,
                file_path=path,
                old_string=old_string,
                new_string=new_string,
            ),
        )

    async def Glob(pattern: str | None = None, path: str | None = None, **input: Any) -> Any:  # noqa: N802
        return await _tool("Glob", **_with_positional(input, pattern=pattern, path=path))

    async def Grep(pattern: str | None = None, path: str | None = None, **input: Any) -> Any:  # noqa: N802
        return await _tool("Grep", **_with_positional(input, pattern=pattern, path=path))

    async def Bash(command: str | None = None, **input: Any) -> Any:  # noqa: N802
        return await _tool("Bash", **_with_positional(input, command=command))

    async def PowerShell(command: str | None = None, **input: Any) -> Any:  # noqa: N802
        return await _tool("PowerShell", **_with_positional(input, command=command))

    async def Cmd(command: str | None = None, **input: Any) -> Any:  # noqa: N802
        return await _tool("Cmd", **_with_positional(input, command=command))

    async def Python(code: str | None = None, **input: Any) -> Any:  # noqa: N802
        return await _tool("Python", **_with_positional(input, code=code))

    async def Agent(prompt: str | None = None, **input: Any) -> Any:  # noqa: N802
        return await _tool("Agent", **_with_positional(input, prompt=prompt))

    async def sh(command: str, shell: str | None = None, timeout_ms: int | None = None) -> str:
        shell_name = _shell_tool_name(shell)
        payload: dict[str, Any] = {"command": command}
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        return str(await _tool(shell_name, **payload))

    async def cat(path: str, offset: int | None = None, limit: int | None = None) -> str:
        payload: dict[str, Any] = {"file_path": path}
        if offset is not None:
            payload["offset"] = offset
        if limit is not None:
            payload["limit"] = limit
        return str(await _tool("Read", **payload))

    async def rg(pattern: str, path: str | None = None, **opts: Any) -> str:
        payload = {"pattern": pattern, **opts}
        if path is not None:
            payload["path"] = path
        return str(await _tool("Grep", **payload))

    async def rgf(pattern: str, path: str | None = None, glob: str | None = None) -> list[str]:
        payload: dict[str, Any] = {"pattern": pattern}
        if path is not None:
            payload["path"] = path
        if glob is not None:
            payload["glob"] = glob
        try:
            text = str(await _tool("Grep", **payload))
        except Exception:
            return []
        return [line.split(":", 1)[0] for line in text.splitlines() if ":" in line]

    async def gl(pattern: str, path: str | None = None) -> list[str]:
        payload: dict[str, Any] = {"pattern": pattern}
        if path is not None:
            payload["path"] = path
        try:
            text = str(await _tool("Glob", **payload))
        except Exception:
            return []
        return [line.strip() for line in text.splitlines() if line.strip()]

    async def put(path: str, content: str) -> str:
        return str(await _tool("Write", file_path=path, content=content))

    def chdir(path: str) -> None:
        import os

        os.chdir(path)

    return {
        "__builtins__": {
            "Exception": Exception,
            "False": False,
            "True": True,
            "None": None,
            "bool": bool,
            "dict": dict,
            "enumerate": enumerate,
            "float": float,
            "int": int,
            "len": len,
            "list": list,
            "max": max,
            "min": min,
            "print": print,
            "range": range,
            "repr": repr,
            "set": set,
            "sorted": sorted,
            "str": str,
            "sum": sum,
            "tuple": tuple,
        },
        "Agent": Agent,
        "Bash": Bash,
        "Cmd": Cmd,
        "Edit": Edit,
        "Glob": Glob,
        "Grep": Grep,
        "PowerShell": PowerShell,
        "Python": Python,
        "Read": Read,
        "Write": Write,
        "cat": cat,
        "chdir": chdir,
        "gl": gl,
        "put": put,
        "rg": rg,
        "rgf": rgf,
        "sh": sh,
    }


def _shell_tool_name(shell: str | None) -> str:
    if shell:
        normalized = shell.strip().lower()
        if normalized in ("cmd", "cmd.exe"):
            return "Cmd"
        if normalized in ("powershell", "pwsh"):
            return "PowerShell"
        if normalized in ("bash", "sh"):
            return "Bash"
    return "PowerShell" if sys.platform == "win32" else "Bash"


async def _call_parent_tool(in_q: Queue, out_q: Queue, tool_name: str, tool_input: dict[str, Any]) -> Any:
    import os

    call_id = uuid.uuid4().hex
    _safe_put(
        out_q,
        {
            "type": "tool_call",
            "id": call_id,
            "tool": tool_name,
            "input": tool_input,
            "cwd": os.getcwd(),
        },
    )
    result = await asyncio.to_thread(_wait_for_tool_result, in_q, call_id)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("text") or result.get("error") or "tool call failed"))
    return result.get("text", "")


def _wait_for_tool_result(in_q: Queue, call_id: str) -> dict[str, Any]:
    while True:
        try:
            msg = in_q.get()
        except (EOFError, KeyboardInterrupt):
            raise RuntimeError("REPL worker interrupted while waiting for tool result") from None
        if isinstance(msg, dict) and msg.get("type") == "tool_result" and msg.get("id") == call_id:
            return msg


def _execute(code: str, globals_: dict[str, Any]) -> dict[str, Any]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        tree = lint_repl_code(code)
        _assign_last_expr_to_o(tree)
        compiled = compile(
            tree,
            "<deepcli-repl>",
            "exec",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        )
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            value = eval(compiled, globals_, globals_)  # noqa: S307 - worker-only REPL execution
            if inspect.isawaitable(value):
                asyncio.run(cast(Coroutine[Any, Any, Any], value))
            globals_["o"] = asyncio.run(_resolve_awaitables(globals_.get("o")))
            result_value = _ipc_safe_value(globals_.get("o"))
            globals_["o"] = result_value
        return {
            "ok": True,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "value": result_value,
        }
    except Exception:
        traceback.print_exc(file=stderr)
        return {
            "ok": False,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "error": stderr.getvalue(),
        }


async def _resolve_awaitables(value: Any, depth: int = 0) -> Any:
    if depth > 20:
        return "<REPL result nesting too deep>"
    if inspect.isawaitable(value):
        return await cast(Coroutine[Any, Any, Any], value)
    if isinstance(value, list):
        return [await _resolve_awaitables(item, depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple([await _resolve_awaitables(item, depth + 1) for item in value])
    if isinstance(value, dict):
        return {
            _ipc_safe_value(key): await _resolve_awaitables(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, set):
        return {await _resolve_awaitables(item, depth + 1) for item in value}
    return value


def _ipc_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_ipc_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_ipc_safe_value(item) for item in value)
    if isinstance(value, dict):
        return {_ipc_safe_key(key): _ipc_safe_value(item) for key, item in value.items()}
    if isinstance(value, set):
        return [_ipc_safe_value(item) for item in value]
    try:
        pickle.dumps(value)
        return value
    except Exception:
        return repr(value)


def _ipc_safe_key(value: Any) -> Any:
    safe = _ipc_safe_value(value)
    try:
        hash(safe)
        return safe
    except Exception:
        return repr(safe)


def _safe_put(out_q: Queue, message: dict[str, Any]) -> None:
    safe_message = _ipc_safe_value(message)
    out_q.put(safe_message)


def _assign_last_expr_to_o(tree: ast.Module) -> None:
    if not tree.body or not isinstance(tree.body[-1], ast.Expr):
        return
    expr = tree.body[-1]
    tree.body[-1] = ast.Assign(
        targets=[ast.Name(id="o", ctx=ast.Store())],
        value=expr.value,
        lineno=getattr(expr, "lineno", 1),
        col_offset=getattr(expr, "col_offset", 0),
    )
    ast.fix_missing_locations(tree)


__all__ = ["worker_main"]
