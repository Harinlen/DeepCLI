"""Hook discovery + handler module import.

Walks the two source layers (user / project), parses each candidate
hook directory's ``HOOK.md``, runs eligibility filtering, performs a
realpath boundary check, and dynamically imports ``handler.py``.

Outputs a list of :class:`LoadedHook` records — one per hook directory
that survived all filters.  ``HookManager`` then walks the list and
calls ``HookRegistry.register(event, handler)`` for each declared event.

A hook that fails to load (malformed manifest, missing handler symbol,
boundary violation, …) is logged and skipped.  One bad hook never
prevents the others from loading, mirroring OpenClaw's per-entry
try/catch policy.
"""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from kernel.hooks.eligibility import is_eligible
from kernel.hooks.manifest import HookManifest, ManifestError, parse_manifest
from kernel.hooks.types import HookEvent, HookHandler

logger = logging.getLogger(__name__)


# Convention-fixed file names — never user-overridable.  See
# docs/plans/landed/hook-manager.md §7.2.1.
_MANIFEST_FILENAME = "HOOK.md"
_HANDLER_FILENAME = "handler.py"
_HANDLER_FUNC_NAME = "handle"


@dataclass(frozen=True)
class LoadedHook:
    """A hook directory that made it through discovery + import."""

    manifest: HookManifest
    handler: HookHandler
    layer: str
    """``"user"`` or ``"project"`` — surfaces in logs and is reserved
    for future per-layer policy hooks (e.g. opt-in gating)."""

    events: tuple[HookEvent, ...]
    """Already mapped from manifest.events strings; unknown event
    names cause the loader to drop the entire hook before this
    LoadedHook is constructed."""


def discover(
    *,
    user_dir: Path,
    project_dir: Path | None,
    project_enabled: Iterable[str],
) -> list[LoadedHook]:
    """Walk both layers and return the loadable hooks.

    Parameters
    ----------
    user_dir:
        Typically ``~/.mustang/hooks``.  All hooks here load by
        default — no opt-in needed.  Missing directory is fine.
    project_dir:
        Typically ``<cwd>/.mustang/hooks``.  ``None`` disables
        project-layer discovery entirely.
    project_enabled:
        Hook-id allow-list for the project layer.  A project hook
        whose directory name is not in this iterable is silently
        skipped — security gate against ``git clone evil/repo``
        autoloading hostile in-process code.
    """
    loaded: list[LoadedHook] = []
    loaded.extend(
        _discover_layer(
            base_dir=user_dir,
            layer="user",
            opt_in=None,  # user layer = no opt-in
        )
    )
    if project_dir is not None:
        loaded.extend(
            _discover_layer(
                base_dir=project_dir,
                layer="project",
                opt_in=set(project_enabled),
            )
        )
    return loaded


def _discover_layer(
    *,
    base_dir: Path,
    layer: str,
    opt_in: set[str] | None,
) -> list[LoadedHook]:
    """Inner walker for a single source directory.

    ``opt_in is None`` means "load everything" (user layer).  An empty
    or populated set means "only load matching directory names"
    (project layer with explicit-opt-in policy).
    """
    if not base_dir.is_dir():
        # Missing dir is the common case — empty hooks system.
        return []

    out: list[LoadedHook] = []
    # Sort for deterministic load + log order across runs.
    for hook_dir in sorted(base_dir.iterdir()):
        if not hook_dir.is_dir():
            continue
        if opt_in is not None and hook_dir.name not in opt_in:
            logger.debug(
                "hooks: skipping project hook %s — not in project_hooks.enabled",
                hook_dir.name,
            )
            continue

        loaded = _try_load_hook(hook_dir, layer=layer)
        if loaded is not None:
            out.append(loaded)
    return out


def _try_load_hook(hook_dir: Path, *, layer: str) -> LoadedHook | None:
    """Parse + filter + import a single hook directory.

    Returns ``None`` and logs the reason when any step fails — never
    raises.  Per-hook failures must not stop discovery of siblings.
    """
    # 1. Parse manifest (raises ManifestError on bad HOOK.md).
    try:
        manifest = parse_manifest(hook_dir)
    except ManifestError as exc:
        logger.warning("hooks: skipping %s — %s", hook_dir.name, exc)
        return None

    # 2. Eligibility (OS / bins / env).  Skip silently on failure with
    # an info-level reason so operators can grep their logs.
    eligible, reason = is_eligible(manifest)
    if not eligible:
        logger.info("hooks: skipping %s — %s", manifest.name, reason)
        return None

    # 3. Boundary check: handler.py must resolve inside the hook dir.
    # Defends against symlink escapes (accidental, not adversarial —
    # in-process trust model assumes local code is benign).
    try:
        real_dir = hook_dir.resolve(strict=True)
        real_handler = manifest.handler_path.resolve(strict=True)
    except FileNotFoundError as exc:
        logger.warning("hooks: skipping %s — path resolution failed: %s", manifest.name, exc)
        return None
    if not _is_path_inside(real_handler, real_dir):
        logger.warning(
            "hooks: skipping %s — handler.py resolves outside hook dir (%s -> %s)",
            manifest.name,
            manifest.handler_path,
            real_handler,
        )
        return None

    # 4. Map event strings → HookEvent enum.  Unknown event names
    # disqualify the entire hook (vs. silently dropping the bad event)
    # so the operator notices a typo at startup.
    try:
        events = tuple(HookEvent(e) for e in manifest.events)
    except ValueError as exc:
        logger.warning("hooks: skipping %s — unknown event in events list: %s", manifest.name, exc)
        return None

    # 5. Dynamically import handler.py and resolve the ``handle`` symbol.
    handler = _import_handler(manifest, layer=layer)
    if handler is None:
        # _import_handler logs the specific reason.
        return None

    logger.info(
        "hooks: loaded %s [%s] -> %s",
        manifest.name,
        layer,
        ", ".join(e.value for e in events),
    )
    return LoadedHook(manifest=manifest, handler=handler, layer=layer, events=events)


def _import_handler(manifest: HookManifest, *, layer: str) -> HookHandler | None:
    """Import ``handler.py`` and return its top-level ``handle`` callable.

    Module name is namespaced ``_mustang_hook_<layer>_<name>`` to keep
    sys.modules collisions away from arbitrary user code that may also
    use generic module names.  Re-import under the same name (during
    tests) replaces the previous entry — handler files are the source
    of truth.
    """
    spec = importlib.util.spec_from_file_location(
        f"_mustang_hook_{layer}_{manifest.name}",
        manifest.handler_path,
    )
    if spec is None or spec.loader is None:
        logger.warning(
            "hooks: skipping %s — could not create import spec for %s",
            manifest.name,
            manifest.handler_path,
        )
        return None

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        logger.exception("hooks: skipping %s — handler.py failed to import", manifest.name)
        return None

    handler = getattr(module, _HANDLER_FUNC_NAME, None)
    if handler is None or not callable(handler):
        logger.warning(
            "hooks: skipping %s — handler.py must define top-level '%s' callable",
            manifest.name,
            _HANDLER_FUNC_NAME,
        )
        return None
    return handler


def _is_path_inside(candidate: Path, root: Path) -> bool:
    """Check whether ``candidate`` is at or under ``root`` after both
    are resolved.

    Uses ``Path.is_relative_to`` (3.9+).  Both inputs are expected to
    already be ``.resolve(strict=True)``-ed by the caller so this is a
    pure path comparison.
    """
    try:
        return candidate.is_relative_to(root)
    except ValueError:
        return False


__all__ = ["LoadedHook", "discover"]
