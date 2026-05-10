"""Shared foreground shell process execution for built-in shell tools."""

from __future__ import annotations

import asyncio
import os
import signal
import shutil
import subprocess
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from kernel.core.protocol.interfaces.contracts.text_block import TextBlock
from kernel.agents.mustang.tools.types import TextDisplay, ToolCallProgress, ToolCallResult


@dataclass(frozen=True)
class ShellSpec:
    """Concrete shell invocation selected by a shell tool."""

    argv: Sequence[str] | None = None
    command: str | None = None


def bash_shell_spec(command: str) -> ShellSpec | None:
    """Return a Bash-compatible shell spec, or ``None`` when unavailable."""
    shell = shutil.which("bash") or shutil.which("sh")
    if shell is None:
        return None
    return ShellSpec(argv=[shell, "-lc", command])


def platform_shell_spec(command: str) -> ShellSpec | None:
    """Return the native shell spec for long-running background commands."""
    if os.name == "nt":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if pwsh is not None:
            return ShellSpec(argv=[pwsh, "-NoProfile", "-NonInteractive", "-Command", command])
        cmd = shutil.which("cmd.exe") or shutil.which("cmd")
        if cmd is not None:
            return ShellSpec(argv=[cmd, "/d", "/s", "/c", command])
    return bash_shell_spec(command)


async def run_shell_command(
    spec: ShellSpec,
    *,
    cwd: Path,
    env: Mapping[str, str] | None,
    timeout_ms: int,
) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
    """Run a foreground shell command and stream text progress frames."""

    kwargs = _process_group_kwargs()
    if spec.argv is not None:
        process = await asyncio.create_subprocess_exec(
            *spec.argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env={**os.environ, **env} if env else None,
            **kwargs,
        )
    elif spec.command is not None:
        process = await asyncio.create_subprocess_shell(
            spec.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env={**os.environ, **env} if env else None,
            **kwargs,
        )
    else:
        raise ValueError("ShellSpec must provide argv or command")

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()

    async def _read_stream(stream: asyncio.StreamReader | None, name: str) -> None:
        if stream is None:
            await queue.put((name, None))
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                await queue.put((name, None))
                return
            text = chunk.decode("utf-8", errors="replace")
            await queue.put((name, text))

    readers = [
        asyncio.create_task(_read_stream(process.stdout, "stdout")),
        asyncio.create_task(_read_stream(process.stderr, "stderr")),
    ]
    wait_task = asyncio.create_task(process.wait())

    streams_done: set[str] = set()
    timeout = timeout_ms / 1000.0
    deadline = asyncio.get_running_loop().time() + timeout

    try:
        while len(streams_done) < 2:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            stream, text = await asyncio.wait_for(queue.get(), timeout=remaining)
            if text is None:
                streams_done.add(stream)
                continue
            if stream == "stderr":
                stderr_parts.append(text)
            else:
                stdout_parts.append(text)
            yield ToolCallProgress(content=[TextBlock(type="text", text=text)])

        exit_code = await asyncio.wait_for(wait_task, timeout=max(0.1, deadline - asyncio.get_running_loop().time()))
    except asyncio.TimeoutError:
        await _terminate_process(process)
        error = f"command timed out after {timeout_ms}ms"
        yield _shell_result(-1, "".join(stdout_parts), "".join(stderr_parts) + error)
        return
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise
    finally:
        for reader in readers:
            reader.cancel()

    yield _shell_result(exit_code or 0, "".join(stdout_parts), "".join(stderr_parts))


async def spawn_shell_background(
    spec: ShellSpec,
    *,
    cwd: Path,
    env: Mapping[str, str] | None,
    stdout: int,
    stderr: int,
) -> asyncio.subprocess.Process:
    """Spawn a shell process whose output is owned by caller-provided fds."""
    kwargs = _process_group_kwargs()
    if spec.argv is not None:
        return await asyncio.create_subprocess_exec(
            *spec.argv,
            stdout=stdout,
            stderr=stderr,
            cwd=str(cwd),
            env={**os.environ, **env} if env else None,
            **kwargs,
        )
    if spec.command is not None:
        return await asyncio.create_subprocess_shell(
            spec.command,
            stdout=stdout,
            stderr=stderr,
            cwd=str(cwd),
            env={**os.environ, **env} if env else None,
            **kwargs,
        )
    raise ValueError("ShellSpec must provide argv or command")


def _shell_result(exit_code: int, stdout: str, stderr: str) -> ToolCallResult:
    body_parts = []
    if stdout:
        body_parts.append(stdout.rstrip())
    if stderr:
        body_parts.append(f"[stderr]\n{stderr.rstrip()}")
    if exit_code != 0:
        body_parts.append(f"[exit {exit_code}]")
    body = "\n".join(body_parts) if body_parts else "(no output)"
    return ToolCallResult(
        data={"exit_code": exit_code, "stdout": stdout, "stderr": stderr},
        llm_content=[TextBlock(type="text", text=body)],
        display=TextDisplay(text=body, language="shell-session"),
    )


def _process_group_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except asyncio.TimeoutError:
        if process.returncode is None:
            process.kill()
            await process.wait()


__all__ = [
    "ShellSpec",
    "bash_shell_spec",
    "platform_shell_spec",
    "run_shell_command",
    "spawn_shell_background",
]
