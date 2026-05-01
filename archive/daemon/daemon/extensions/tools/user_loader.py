"""User-defined tool loader — discover and load tools from ~/.mustang/tools/.

Scans a directory for Python files, dynamically imports them, and finds
classes that inherit from ``Tool``.  Loaded tools are returned for
registration in the tool registry.

Security: rejects symlinks that escape the tools directory and catches
all import/instantiation errors so a broken user tool never crashes
the daemon.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType

from daemon.extensions.tools.base import Tool

logger = logging.getLogger(__name__)

# Sentinel prefix for dynamically loaded user tool modules
_MODULE_PREFIX = "mustang_user_tool_"


def _is_safe_path(file_path: Path, tools_dir: Path) -> bool:
    """Check that *file_path* resolves inside *tools_dir* (no symlink escape).

    Uses ``Path.is_relative_to()`` instead of string prefix matching
    to avoid bypasses like ``/tools-evil/`` matching ``/tools``.
    """
    try:
        resolved = file_path.resolve(strict=True)
        parent_resolved = tools_dir.resolve(strict=True)
        return resolved.is_relative_to(parent_resolved)
    except OSError:
        return False


def _import_module_from_path(file_path: Path) -> ModuleType | None:
    """Import a Python file as a module.  Returns None on failure."""
    module_name = f"{_MODULE_PREFIX}{file_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        logger.warning("Cannot create module spec for %s", file_path)
        return None

    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules so relative imports within the file work
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        logger.exception("Failed to import user tool from %s", file_path)
        # Clean up partial registration
        sys.modules.pop(module_name, None)
        return None
    return module


def _find_tool_classes(module: ModuleType) -> list[type[Tool]]:
    """Find all concrete Tool subclasses defined in *module*."""
    classes: list[type[Tool]] = []
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        if (
            isinstance(obj, type)
            and issubclass(obj, Tool)
            and obj is not Tool
            # Only classes defined in this module, not re-imported ones
            and obj.__module__ == module.__name__
        ):
            classes.append(obj)
    return classes


def load_user_tools(tools_dir: Path) -> list[Tool]:
    """Discover and load user-defined tools from a directory.

    Scans ``tools_dir`` for ``*.py`` files, imports each one, finds
    ``Tool`` subclasses, instantiates them, and returns the list.

    Errors in individual files are logged and skipped — one broken
    tool does not prevent others from loading.

    Args:
        tools_dir: Path to scan (typically ``~/.mustang/tools/``).

    Returns:
        List of successfully instantiated Tool instances.
    """
    if not tools_dir.is_dir():
        logger.debug("User tools directory does not exist: %s", tools_dir)
        return []

    tools: list[Tool] = []
    py_files = sorted(tools_dir.glob("*.py"))

    for file_path in py_files:
        # Skip __init__.py and hidden files
        if file_path.name.startswith("_") or file_path.name.startswith("."):
            continue

        # Security: reject symlinks that escape the tools directory
        if not _is_safe_path(file_path, tools_dir):
            logger.warning(
                "Skipping %s: resolves outside tools directory (possible symlink escape)",
                file_path,
            )
            continue

        module = _import_module_from_path(file_path)
        if module is None:
            continue

        tool_classes = _find_tool_classes(module)
        if not tool_classes:
            logger.debug("No Tool subclasses found in %s", file_path)
            continue

        for cls in tool_classes:
            try:
                instance = cls()
                # Validate required attributes
                if not instance.name or not instance.description:
                    logger.warning(
                        "Skipping %s from %s: missing name or description",
                        cls.__name__,
                        file_path,
                    )
                    continue
                tools.append(instance)
                logger.info("Loaded user tool '%s' from %s", instance.name, file_path)
            except Exception:
                logger.exception("Failed to instantiate %s from %s", cls.__name__, file_path)

    return tools
