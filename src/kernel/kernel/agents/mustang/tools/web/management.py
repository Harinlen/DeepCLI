"""WebFetch backend inventory, configuration, and setup helpers."""

from __future__ import annotations

import asyncio
import importlib.util
import importlib.machinery
import os
from pathlib import Path
import shlex
import shutil
import sys
from dataclasses import dataclass
from typing import Any

from kernel.agents.mustang.tools.web.config import WebFetchBackendName, WebFetchConfig


@dataclass(frozen=True, slots=True)
class BackendDefinition:
    id: WebFetchBackendName
    label: str
    category: str
    cost: str
    role: str
    requires_api_key: bool = False
    env_keys: tuple[str, ...] = ()
    python_modules: tuple[str, ...] = ()
    python_paths: tuple[str, ...] = ()
    setup_commands: tuple[tuple[str, ...], ...] = ()
    setup_env: dict[str, str] | None = None


def _deepcli_home() -> Path:
    return Path(os.getenv("DEEPCLI_HOME", Path.home() / ".deepcli")).expanduser()


def _deepcli_setup_env() -> dict[str, str]:
    home = _deepcli_home()
    return {
        "CRAWL4_AI_BASE_DIRECTORY": str(home),
        "PLAYWRIGHT_BROWSERS_PATH": str(home / "cache" / "ms-playwright"),
        "UV_CACHE_DIR": str(home / "cache" / "uv"),
        "XDG_CACHE_HOME": str(home / "cache" / "xdg"),
    }


def _deepcli_package_dir(package: str) -> Path:
    return _deepcli_home() / "packages" / package


def _with_pythonpath(env: dict[str, str], paths: tuple[str, ...]) -> dict[str, str]:
    if not paths:
        return env
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([*paths, *([current] if current else [])])
    return env


def _activate_python_paths(paths: tuple[str, ...]) -> None:
    for path in reversed(paths):
        if path and path not in sys.path:
            sys.path.insert(0, path)


def _private_uv_candidates() -> list[Path]:
    candidates: list[Path] = []
    for parent in Path(sys.executable).resolve().parents:
        uv_root = parent / "tools" / "uv"
        if not uv_root.is_dir():
            continue
        candidates.extend(sorted(uv_root.glob("*/uv*"), reverse=True))
    return candidates


def _resolve_uv() -> str | None:
    env_uv = os.getenv("DEEPCLI_UV_BIN", "").strip()
    if env_uv and Path(env_uv).is_file():
        return env_uv
    path_uv = shutil.which("uv")
    if path_uv:
        return path_uv
    for candidate in _private_uv_candidates():
        if candidate.is_file():
            return str(candidate)
    return None


def _python_package_install_command(
    requirement: str, *, target: Path | None = None
) -> tuple[str, ...]:
    uv = _resolve_uv()
    if uv:
        command = (uv, "pip", "install", "--python", sys.executable)
        if target is not None:
            command = (*command, "--target", str(target))
        return (*command, requirement)
    command = (sys.executable, "-m", "pip", "install")
    if target is not None:
        command = (*command, "--target", str(target))
    return (*command, requirement)


def _python_module_command(module: str, *args: str) -> tuple[str, ...]:
    return (sys.executable, "-m", module, *args)


def _python_code_command(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


BACKEND_DEFINITIONS: tuple[BackendDefinition, ...] = (
    BackendDefinition(
        id="auto",
        label="Auto",
        category="builtin",
        cost="free",
        role="Use configured local and service backends in fallback order",
    ),
    BackendDefinition(
        id="httpx",
        label="HTTPX",
        category="builtin-local",
        cost="free",
        role="Direct HTTP fetch with JSON/text handling and internal readability extraction",
    ),
    BackendDefinition(
        id="crawl4ai",
        label="Crawl4AI",
        category="optional-local-browser",
        cost="free software, local compute",
        role="Local browser rendering for JavaScript-heavy pages",
        python_modules=("crawl4ai",),
        python_paths=(str(_deepcli_package_dir("crawl4ai")),),
        setup_commands=(
            _python_package_install_command(
                "crawl4ai>=0.6.3",
                target=_deepcli_package_dir("crawl4ai"),
            ),
            _python_module_command("playwright", "install", "chromium"),
            _python_module_command("patchright", "install", "chromium"),
            _python_code_command(
                "from crawl4ai.install import setup_home_directory, run_migration; "
                "setup_home_directory(); run_migration()"
            ),
        ),
        setup_env=_deepcli_setup_env(),
    ),
    BackendDefinition(
        id="firecrawl",
        label="Firecrawl",
        category="external-service",
        cost="free tier / paid",
        role="Cloud fallback for difficult pages",
        requires_api_key=True,
        env_keys=("FIRECRAWL_API_KEY", "FIRECRAWL_API_URL"),
    ),
    BackendDefinition(
        id="parallel",
        label="Parallel",
        category="external-service",
        cost="paid/API-key",
        role="Provider extraction",
        requires_api_key=True,
        env_keys=("PARALLEL_API_KEY",),
    ),
    BackendDefinition(
        id="exa",
        label="Exa",
        category="external-service",
        cost="paid/API-key",
        role="Provider extraction",
        requires_api_key=True,
        env_keys=("EXA_API_KEY",),
    ),
    BackendDefinition(
        id="tavily",
        label="Tavily",
        category="external-service",
        cost="paid/API-key",
        role="Provider extraction",
        requires_api_key=True,
        env_keys=("TAVILY_API_KEY",),
    ),
)

_DEFINITIONS_BY_ID = {definition.id: definition for definition in BACKEND_DEFINITIONS}


def backend_ids() -> set[str]:
    return set(_DEFINITIONS_BY_ID)


def get_definition(backend: str) -> BackendDefinition | None:
    return _DEFINITIONS_BY_ID.get(backend)  # type: ignore[arg-type]


def backend_is_installed(definition: BackendDefinition) -> bool:
    for module in definition.python_modules:
        spec = importlib.util.find_spec(module)
        if spec is None and definition.python_paths:
            spec = importlib.machinery.PathFinder.find_spec(module, list(definition.python_paths))
        if spec is None:
            return False
    return True


def backend_has_credentials(definition: BackendDefinition) -> bool:
    if not definition.requires_api_key:
        return True
    return any(bool(os.getenv(key, "").strip()) for key in definition.env_keys)


def primary_api_key_env(definition: BackendDefinition) -> str | None:
    if not definition.requires_api_key:
        return None
    for key in definition.env_keys:
        if key.endswith("_API_KEY"):
            return key
    return definition.env_keys[0] if definition.env_keys else None


def credential_request(definition: BackendDefinition) -> dict[str, Any] | None:
    env_key = primary_api_key_env(definition)
    if env_key is None:
        return None
    return {
        "backend": definition.id,
        "kind": "api_key",
        "label": f"{definition.label} API key",
        "envKey": env_key,
        "secretName": f"web_fetch.{definition.id}.api_key",
        "prompt": f"Enter {definition.label} API key",
    }


def backend_is_available(definition: BackendDefinition) -> bool:
    if definition.id in {"auto", "httpx"}:
        return True
    return backend_is_installed(definition) and backend_has_credentials(definition)


def build_backend_options(config: WebFetchConfig) -> dict[str, Any]:
    options: list[dict[str, Any]] = []
    for definition in BACKEND_DEFINITIONS:
        installed = backend_is_installed(definition)
        credentials = backend_has_credentials(definition)
        setup_required = bool(definition.setup_commands) and not installed
        if config.backend == definition.id:
            status = "current"
        elif setup_required:
            status = "setup_needed"
        elif definition.requires_api_key and not credentials:
            status = "api_key_needed"
        elif definition.requires_api_key and credentials:
            status = "configured"
        elif backend_is_available(definition):
            status = "available"
        else:
            status = "unavailable"
        available = status in {"current", "available"}
        options.append(
            {
                "id": definition.id,
                "label": definition.label,
                "category": definition.category,
                "cost": definition.cost,
                "role": definition.role,
                "status": status,
                "installed": installed,
                "hasCredentials": credentials,
                "available": available,
                "setupRequired": setup_required,
                "setupPlan": build_setup_plan(definition) if setup_required else None,
                "credentialRequired": definition.requires_api_key and not credentials,
                "credentialRequest": credential_request(definition)
                if definition.requires_api_key and not credentials
                else None,
                "current": config.backend == definition.id,
            }
        )
    return {
        "current": config.backend,
        "options": options,
    }


def build_setup_plan(definition: BackendDefinition) -> dict[str, Any] | None:
    if not definition.setup_commands:
        return None
    return {
        "backend": definition.id,
        "commands": [shlex.join(command) for command in definition.setup_commands],
        "reason": f"{definition.label} dependencies are not installed.",
    }


async def run_setup(definition: BackendDefinition) -> dict[str, Any]:
    """Run an allowlisted backend setup plan.

    This is only called after the CLI has asked for explicit user
    confirmation.  Commands are fixed by ``BACKEND_DEFINITIONS``; user
    input never becomes argv.
    """

    logs: list[dict[str, Any]] = []
    env = os.environ.copy()
    if definition.setup_env:
        env.update(definition.setup_env)
        for value in definition.setup_env.values():
            Path(value).mkdir(parents=True, exist_ok=True)
    if definition.python_paths:
        for path in definition.python_paths:
            Path(path).mkdir(parents=True, exist_ok=True)
        _with_pythonpath(env, definition.python_paths)
    for command in definition.setup_commands:
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            logs.append(
                {
                    "command": " ".join(command),
                    "exitCode": -1,
                    "stderr": str(exc),
                }
            )
            return {"ok": False, "logs": logs}
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logs.append(
                {
                    "command": " ".join(command),
                    "exitCode": -1,
                    "stderr": "setup command timed out",
                }
            )
            return {"ok": False, "logs": logs}
        log = {
            "command": " ".join(command),
            "exitCode": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace")[-4000:],
            "stderr": stderr.decode("utf-8", errors="replace")[-4000:],
        }
        logs.append(log)
        if proc.returncode != 0:
            return {"ok": False, "logs": logs}
    return {"ok": True, "logs": logs}


__all__ = [
    "BACKEND_DEFINITIONS",
    "BackendDefinition",
    "backend_ids",
    "backend_is_available",
    "backend_is_installed",
    "build_backend_options",
    "build_setup_plan",
    "credential_request",
    "get_definition",
    "primary_api_key_env",
    "run_setup",
]
