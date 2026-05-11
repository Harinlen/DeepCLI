"""Import-boundary checks for the Kernel agent layout."""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
KERNEL_ROOT = PROJECT_ROOT / "src" / "kernel" / "kernel"

LEGACY_TOP_LEVEL_PACKAGES = {
    "access_agent",
    "agent_runtime",
    "commands",
    "config",
    "connection_auth",
    "flags",
    "gateways",
    "git",
    "hooks",
    "llm",
    "llm_provider",
    "mcp",
    "memory",
    "orchestrator",
    "prompts",
    "protocol",
    "schedule",
    "secrets",
    "session",
    "skills",
    "tasks",
    "tool_authz",
    "tools",
}

EXPECTED_ROOT_ENTRIES = {
    "__init__.py",
    "__main__.py",
    "agent_hub",
    "agents",
    "core",
    "supervisor",
    "uvicorn_runtime.py",
}


def _python_files(*roots: Path) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    return sorted(files)


def _kernel_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names if alias.name.startswith("kernel."))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("kernel."):
            imports.append(node.module)
    return imports


def test_kernel_root_contains_only_owner_directories() -> None:
    entries = {
        path.name
        for path in KERNEL_ROOT.iterdir()
        if path.name != "__pycache__" and not path.name.endswith(".pyc")
    }
    assert entries == EXPECTED_ROOT_ENTRIES
    assert entries.isdisjoint(LEGACY_TOP_LEVEL_PACKAGES)


def test_legacy_top_level_kernel_imports_do_not_return() -> None:
    legacy_prefixes = tuple(f"kernel.{name}" for name in LEGACY_TOP_LEVEL_PACKAGES)
    offenders: list[str] = []

    for path in _python_files(PROJECT_ROOT / "src", PROJECT_ROOT / "tests"):
        for imported in _kernel_imports(path):
            if imported.startswith(legacy_prefixes):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)} imports {imported}")

    assert offenders == []


def test_supervisor_stays_out_of_agent_implementation_packages() -> None:
    forbidden = ("kernel.agents.", "kernel.agent_hub.")
    offenders: list[str] = []

    for path in _python_files(KERNEL_ROOT / "supervisor"):
        for imported in _kernel_imports(path):
            if imported.startswith(forbidden):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)} imports {imported}")

    assert offenders == []


def test_agent_hub_does_not_import_access_agent_internals() -> None:
    offenders: list[str] = []

    for path in _python_files(KERNEL_ROOT / "agent_hub"):
        for imported in _kernel_imports(path):
            if imported.startswith("kernel.agents.access."):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)} imports {imported}")

    assert offenders == []
