"""Hard guard against agent-initiated DeepCLI runtime kills."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from kernel.agents.mustang.tool_authz.constants import SHELL_TOOL_NAMES
from kernel.agents.mustang.tools.matching import matches_name

RUNTIME_DENY_MESSAGE = (
    "DeepCLI runtime lifecycle is protected. Use RestartSelf to restart this agent, "
    "or /kernel restart for a full user-controlled runtime restart. Agents cannot "
    "kill Supervisor or Kernel processes directly."
)

_KILL_PATTERNS = (
    re.compile(r"(^|[;&|]\s*)kill\s+(-[A-Z0-9]+\s+)?(?P<pid>-?\d+)\b", re.IGNORECASE),
    re.compile(r"\bpkill\b.*\bkernel\.(supervisor|agent_hub|access_agent|agent_runtime)\b"),
    re.compile(r"\bkillall\b.*\b(kernel\.supervisor|python)\b"),
    re.compile(r"\btaskkill\b", re.IGNORECASE),
    re.compile(r"\bStop-Process\b", re.IGNORECASE),
)
_RUNTIME_COMMAND_RE = re.compile(
    r"\b(deepcli\s+(stop|restart)|scripts/run-kernel\.(sh|ps1|bat).*(kill|restart))\b",
    re.IGNORECASE,
)
_RUNTIME_PROCESS_RE = re.compile(r"\bkernel\.(supervisor|agent_hub|access_agent|agent_runtime)\b")


def runtime_kill_denial(tool: Any, tool_input: dict[str, Any]) -> str | None:
    """Return a deny message when a shell tool targets DeepCLI runtime."""
    if not _is_shell_tool(tool):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str):
        return None
    if _RUNTIME_COMMAND_RE.search(command):
        return RUNTIME_DENY_MESSAGE
    if _RUNTIME_PROCESS_RE.search(command) and _mentions_kill(command):
        return RUNTIME_DENY_MESSAGE
    protected = _protected_pids()
    for pattern in _KILL_PATTERNS:
        match = pattern.search(command)
        if match is None:
            continue
        pid = match.groupdict().get("pid")
        if pid is None or _pid_matches(pid, protected):
            return RUNTIME_DENY_MESSAGE
    return None


def _is_shell_tool(tool: Any) -> bool:
    return any(
        matches_name(tool, name) for name in SHELL_TOOL_NAMES | {"Cmd", "ShellExec", "Monitor"}
    )


def _mentions_kill(command: str) -> bool:
    return any(word in command.lower() for word in ("kill", "stop-process", "taskkill"))


def _pid_matches(pid_text: str, protected: set[int]) -> bool:
    try:
        pid = abs(int(pid_text))
    except ValueError:
        return False
    return pid in protected


def _protected_pids() -> set[int]:
    pids = {os.getpid(), os.getppid()}
    runtime_file = os.getenv("MUSTANG_SUPERVISOR_RUNTIME_FILE", "")
    if runtime_file:
        pids.update(_pids_from_runtime_file(Path(runtime_file)))
    return {pid for pid in pids if pid > 0}


def _pids_from_runtime_file(path: Path) -> set[int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    pids: set[int] = set()
    _collect_pids(payload, pids)
    return pids


def _collect_pids(value: object, pids: set[int]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "pid":
                try:
                    pids.add(int(item))
                except (TypeError, ValueError):
                    pass
            else:
                _collect_pids(item, pids)
    elif isinstance(value, list):
        for item in value:
            _collect_pids(item, pids)


__all__ = ["RUNTIME_DENY_MESSAGE", "runtime_kill_denial"]
