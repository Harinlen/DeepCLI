"""Shared agent-browser CLI helpers — path resolution, env, shutdown hook.

Both ``page_fetch`` and ``browser`` tools shell out to the
``agent-browser`` binary that ``npm install`` placed under
``src/daemon/node_modules/.bin/``.  Centralised here so the path
constant, env defaults, and shutdown cleanup are defined once.

The agent-browser native daemon (Rust process behind the CLI) starts
lazily on the first call and persists between commands.  We set
``AGENT_BROWSER_IDLE_TIMEOUT_MS`` so it self-terminates after a few
minutes of inactivity, and we register a cleanup hook to call
``agent-browser close --all`` on Mustang daemon shutdown.

**Chrome isolation**: we explicitly pin the Chrome binary by setting
``AGENT_BROWSER_EXECUTABLE_PATH`` to the copy that ``agent-browser
install`` downloaded under ``~/.agent-browser/browsers/``.  Without
this, agent-browser would auto-detect and could silently fall back to
the user's system Chrome if its cache became corrupted.  Pinning to
the bundled Chrome guarantees:

- The browser tools never touch the user's daily Chrome profile,
  cookies, or bookmarks.
- A future "control system Chrome" tool can be added cleanly without
  conflicting with this one — it will explicitly NOT set the env var.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import re
import signal
import sys
from pathlib import Path

from daemon.lifecycle import register_cleanup
from daemon.extensions.tools.builtin.subprocess_utils import run_with_timeout

logger = logging.getLogger(__name__)

# This file lives at:
#   src/daemon/daemon/extensions/tools/builtin/agent_browser_cli.py
# Walk up to src/daemon/ where node_modules/ will be installed.
#   parents[0] = builtin
#   parents[1] = tools
#   parents[2] = extensions
#   parents[3] = daemon  (inner package)
#   parents[4] = daemon  (outer src/daemon dir)
_DAEMON_DIR = Path(__file__).resolve().parents[4]
AGENT_BROWSER_CLI: Path = _DAEMON_DIR / "node_modules" / ".bin" / "agent-browser"

# Where ``agent-browser install`` puts Chrome — mirrors the Rust impl
# at cli/src/install.rs::get_browsers_dir().
_BROWSERS_CACHE = Path.home() / ".agent-browser" / "browsers"

# Idle timeout for the agent-browser native daemon. After this many
# milliseconds with no commands, the daemon closes Chrome and exits.
# 5 minutes — stays warm during active conversations, releases
# resources when idle.
_IDLE_TIMEOUT_MS = 5 * 60 * 1000

# Cap CLI stdout so we don't OOM on huge pages.  agent-browser
# honours this via AGENT_BROWSER_MAX_OUTPUT.
_MAX_OUTPUT_CHARS = 200_000

# Chrome launch flags passed via AGENT_BROWSER_ARGS.
#
# ``--no-sandbox`` is required on:
#   - Ubuntu 23.10+ (and other distros that disable unprivileged user
#     namespaces via AppArmor)
#   - Containers (Docker, k8s)
#   - VMs without proper sandboxing support
#
# Mustang already isolates Chrome from the user (we run a bundled
# Chrome-for-Testing, not the user's daily browser, and the LLM only
# touches it through ``http_fetch`` / ``page_fetch`` / ``browser`` —
# all of which are PROMPT-gated).  Disabling Chrome's own sandbox does
# not change those guarantees, it only removes the OS-level process
# sandbox that Chrome would normally use.  We accept that trade-off in
# exchange for working out-of-the-box on modern Linux.
#
# ``--disable-blink-features=AutomationControlled`` makes
# ``navigator.webdriver`` return ``undefined`` instead of ``true``,
# which lets us read sites that block headless browsers (BOM, many
# news sites, Cloudflare-fronted pages).  Standard "look like a real
# Chrome" trick — recommended by agent-browser's own help text.
_CHROME_ARGS = "--no-sandbox,--disable-blink-features=AutomationControlled"

# Per-platform user-agent template — agent-browser-installed Chrome
# will fill in {major} from the actual installed version.  Pattern
# matches what real Chrome sends when not in headless mode.
if sys.platform == "darwin":
    _UA_TEMPLATE = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/{major}.0.0.0 Safari/537.36"
    )
elif sys.platform == "win32":
    _UA_TEMPLATE = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/{major}.0.0.0 Safari/537.36"
    )
else:
    _UA_TEMPLATE = (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/{major}.0.0.0 Safari/537.36"
    )

# Fallback user-agent when we can't determine the bundled Chrome's
# version.  Generic recent Chrome — better than HeadlessChrome which
# trips bot detection on many sites (BOM, Cloudflare-fronted pages).
_FALLBACK_UA = _UA_TEMPLATE.format(major="140")


def _chrome_binary_in_dir(dir_: Path) -> Path | None:
    """Look for a Chrome binary inside a candidate browsers cache subdir.

    Mirrors agent-browser's Rust impl in cli/src/install.rs.  We check
    the layouts agent-browser ships per-platform.
    """
    if sys.platform == "darwin":
        candidates = [
            dir_ / "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            dir_ / "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            dir_ / "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        ]
    elif sys.platform == "win32":
        candidates = [
            dir_ / "chrome.exe",
            dir_ / "chrome-win64" / "chrome.exe",
        ]
    else:  # linux and friends
        candidates = [
            dir_ / "chrome",
            dir_ / "chrome-linux64" / "chrome",
        ]

    for c in candidates:
        if c.exists():
            return c
    return None


def find_installed_chrome() -> Path | None:
    """Locate the Chrome binary installed by ``agent-browser install``.

    Returns the binary path or ``None`` if no Chrome is present in
    agent-browser's cache.  Used to pin the browser tools to the
    bundled Chrome instead of letting agent-browser auto-detect
    (which could fall back to the user's system Chrome).
    """
    if not _BROWSERS_CACHE.exists():
        return None
    try:
        # Each top-level entry under browsers/ is a version dir.  Pick
        # the most-recently-modified one (newest install wins).
        entries = sorted(
            (p for p in _BROWSERS_CACHE.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError as exc:
        logger.warning("Cannot read agent-browser cache %s: %s", _BROWSERS_CACHE, exc)
        return None

    for entry in entries:
        if (bin_path := _chrome_binary_in_dir(entry)) is not None:
            return bin_path
    return None


# Match agent-browser's cache layout: ``chrome-<MAJOR>.<MINOR>.<...>``.
_CHROME_VERSION_RE = re.compile(r"chrome-(\d+)\.")


def _chrome_major_version(chrome_path: Path) -> int | None:
    """Extract the major version of a Chrome binary from its path.

    agent-browser installs Chrome under
    ``~/.agent-browser/browsers/chrome-<full-version>/``, so the
    version is encoded in the parent directory name.  This avoids the
    cost of subprocess-spawning ``chrome --version`` at startup.

    Returns ``None`` if the path doesn't match the expected layout.
    """
    for parent in chrome_path.parents:
        if (m := _CHROME_VERSION_RE.match(parent.name)):
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def _build_user_agent() -> str:
    """Compose a real Chrome user-agent that matches the bundled Chrome.

    Without overriding the user-agent, headless Chrome sends
    ``HeadlessChrome/<version>`` which trips bot detection on many
    sites (BOM, Cloudflare-fronted pages).  We construct the
    equivalent non-headless string for the same major version.

    Returns a fallback string if the bundled Chrome can't be located
    or the version can't be parsed — the fallback is an older but
    still-recent Chrome major.
    """
    chrome = find_installed_chrome()
    if chrome is None:
        return _FALLBACK_UA
    major = _chrome_major_version(chrome)
    if major is None:
        logger.debug("Could not parse Chrome major version from %s", chrome)
        return _FALLBACK_UA
    return _UA_TEMPLATE.format(major=major)


def env() -> dict[str, str]:
    """Return the environment for invoking the agent-browser CLI.

    Inherits the parent process env, overlays our defaults, and pins
    Chrome to agent-browser's bundled copy when available.  We
    deliberately overwrite any inherited ``AGENT_BROWSER_EXECUTABLE_PATH``
    so the user can't accidentally redirect us to their daily Chrome.
    """
    e: dict[str, str] = {
        **os.environ,
        "AGENT_BROWSER_IDLE_TIMEOUT_MS": str(_IDLE_TIMEOUT_MS),
        "AGENT_BROWSER_MAX_OUTPUT": str(_MAX_OUTPUT_CHARS),
        "AGENT_BROWSER_ARGS": _CHROME_ARGS,
        "AGENT_BROWSER_USER_AGENT": _build_user_agent(),
    }
    chrome = find_installed_chrome()
    if chrome is not None:
        e["AGENT_BROWSER_EXECUTABLE_PATH"] = str(chrome)
    else:
        # Drop any inherited value — we never want to silently fall
        # back to system Chrome from this tool.
        e.pop("AGENT_BROWSER_EXECUTABLE_PATH", None)
    return e


def is_available() -> bool:
    """Return ``True`` if both the CLI and the bundled Chrome exist.

    We require BOTH because the CLI alone is useless without Chrome,
    and we never want to silently fall back to the user's system
    Chrome from these tools.
    """
    return AGENT_BROWSER_CLI.exists() and find_installed_chrome() is not None


_INSTALL_HINT = (
    f"agent-browser is not fully installed.\n"
    f"  1. Run `npm install` in {_DAEMON_DIR} to install the CLI.\n"
    f"  2. Run `{_DAEMON_DIR}/node_modules/.bin/agent-browser install` to "
    f"download a bundled copy of Chrome (~150 MB, one-time).\n"
    f"The browser and page_fetch tools intentionally use this bundled "
    f"Chrome instead of your system browser, so the LLM cannot touch "
    f"your daily browsing profile."
)


def install_hint() -> str:
    """Return the human-readable installation hint."""
    return _INSTALL_HINT


############################################################################
# Stale-daemon reaper
#
# Symptom this solves: if a previous Mustang run left an ``agent-browser``
# Rust daemon alive (crash, kill -9, ``uvicorn --reload``, etc.), or if
# the daemon exited without cleaning up its runtime files, the next
# ``agent-browser open`` from a fresh Mustang may hang for tens of
# seconds against a stuck Unix socket or a half-dead peer.  Users saw
# three back-to-back ``page_fetch`` calls all time out at exactly 30s —
# characteristic of IPC deadlock, not of a slow page.
#
# The Rust daemon keeps its state under a per-user runtime directory:
#
#   Linux:  $XDG_RUNTIME_DIR/agent-browser/<session>.{pid,sock,engine,stream,version}
#   macOS:  $TMPDIR/agent-browser/<session>.{pid,sock,...}   (best guess)
#
# Each ``.pid`` file holds the PID of the Rust daemon for that session.
# On reap we: read the pid, SIGKILL it, remove the state files, and
# then wipe any bundled Chrome children that were orphaned.
############################################################################


def _runtime_state_dir() -> Path | None:
    """Return the directory where the Rust daemon keeps its state files.

    Linux: ``$XDG_RUNTIME_DIR/agent-browser/`` (usually
    ``/run/user/<uid>/agent-browser/``).

    macOS / other: ``$TMPDIR/agent-browser/`` as a best guess.  If the
    directory does not exist we return ``None`` — nothing to reap.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        candidate = Path(xdg) / "agent-browser"
        if candidate.is_dir():
            return candidate

    tmpdir = os.environ.get("TMPDIR", "/tmp")
    candidate = Path(tmpdir) / "agent-browser"
    if candidate.is_dir():
        return candidate

    return None


# Filesystem suffixes the Rust daemon writes into ``<runtime>/agent-browser/``.
# Observed layout: ``<session>.sock``, ``<session>.pid``, ``<session>.engine``,
# ``<session>.stream``, ``<session>.version``.
_STATE_SUFFIXES = (".pid", ".sock", ".engine", ".stream", ".version")


def _process_alive(pid: int) -> bool:
    """Return ``True`` if *pid* is a live process we could signal."""
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but owned by someone else — treat as "not ours".
        return False
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        return True
    return True


def _force_kill_pid(pid: int) -> None:
    """Send SIGKILL to *pid*, swallowing already-gone / permission errors."""
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        logger.debug("Cannot kill pid %s: permission denied (not ours)", pid)
    except OSError as exc:
        logger.debug("Cannot kill pid %s: %s", pid, exc)


def _read_pidfile(path: Path) -> int | None:
    """Return the integer pid stored in *path*, or ``None`` on any error."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        return int(raw.split()[0])
    except ValueError:
        return None


def _kill_bundled_chrome_orphans() -> int:
    """Kill any Chrome processes spawned from the bundled browsers cache.

    Matches by executable path prefix (``~/.agent-browser/browsers/``)
    so we never touch the user's daily Chrome at ``/opt/google/chrome``
    or similar.  Read-only best effort — returns the number of kills
    issued for logging.
    """
    cache_str = str(_BROWSERS_CACHE)
    killed = 0
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        # /proc not available (non-Linux, container, sandbox).
        return 0
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            exe = os.readlink(entry / "exe")
        except OSError:
            continue
        if not exe.startswith(cache_str):
            continue
        try:
            pid = int(entry.name)
        except ValueError:
            continue
        _force_kill_pid(pid)
        killed += 1
    return killed


async def reap_stale_daemon() -> int:
    """Kill any stale agent-browser daemons + orphaned bundled Chrome.

    Called at Mustang startup (before :func:`preheat`) and as a
    belt-and-suspenders fallback during shutdown.  The function is
    idempotent and best-effort: it logs but never raises.

    Returns the number of processes killed (for observability).
    """
    total_killed = 0

    state_dir = _runtime_state_dir()
    if state_dir is not None:
        try:
            pidfiles = sorted(state_dir.glob(f"*{_STATE_SUFFIXES[0]}"))
        except OSError:
            pidfiles = []

        for pidfile in pidfiles:
            pid = _read_pidfile(pidfile)
            if pid is None:
                # Unreadable / empty pidfile — just unlink.
                pidfile.unlink(missing_ok=True)
                continue
            if _process_alive(pid):
                logger.info(
                    "Reaping stale agent-browser daemon pid=%s from %s",
                    pid,
                    pidfile.name,
                )
                _force_kill_pid(pid)
                total_killed += 1
                # Give the kernel a moment to reap the process so
                # subsequent glob / socket re-bind succeed cleanly.
                for _ in range(20):  # up to ~1s
                    if not _process_alive(pid):
                        break
                    await asyncio.sleep(0.05)

            # Remove every state file belonging to this session —
            # ``<session>.pid`` is the anchor, the rest are siblings
            # with the same stem.
            stem = pidfile.stem
            for suffix in _STATE_SUFFIXES:
                (state_dir / f"{stem}{suffix}").unlink(missing_ok=True)

    # Even if no pidfile was present, bundled Chrome children may be
    # orphaned (parent daemon died before writing its pidfile, or the
    # pidfile was already cleared by a previous reap).  Walk /proc
    # and SIGKILL anything running the bundled Chrome binary.
    chrome_killed = _kill_bundled_chrome_orphans()
    if chrome_killed:
        logger.info("Reaped %d orphan bundled-Chrome process(es)", chrome_killed)
    total_killed += chrome_killed

    return total_killed


async def _close_browser_on_shutdown() -> None:
    """Run on daemon shutdown — graceful close + reap any survivors.

    Strategy:
      1. Try ``agent-browser close --all`` with a short timeout so
         the Rust daemon gets a chance to persist state / run its
         own teardown.
      2. Then call :func:`reap_stale_daemon` unconditionally.  If the
         graceful path worked there is nothing to kill; if it hung
         (which is what ``reap_stale_daemon`` is here for) we force-
         kill the daemon + Chrome children so they cannot haunt the
         next Mustang run.

    Never raises.
    """
    if is_available():
        try:
            await run_with_timeout(
                [str(AGENT_BROWSER_CLI), "close", "--all"],
                cwd="/",
                timeout_s=5,
                env=env(),
            )
        except Exception:
            logger.debug("agent-browser close --all failed (best-effort)")

    try:
        await reap_stale_daemon()
    except Exception:
        logger.debug("stale-daemon reap on shutdown failed", exc_info=True)


# Preheat budget — wide enough to cover a genuine Chrome cold start
# (process spawn + CDP connect + about:blank nav) on a slow machine.
# The real page_fetch timeout then only has to cover navigation cost,
# not cold-start cost.
_PREHEAT_TIMEOUT_S = 60.0


async def preheat() -> None:
    """Warm up the agent-browser daemon + Chrome in the background.

    Invokes ``agent-browser open about:blank`` once.  That spawns a
    fresh Rust daemon, launches the bundled Chrome via CDP, and
    leaves an idle blank page ready.  The next ``open <url>`` call
    from ``page_fetch`` / ``browser`` then only pays navigation cost —
    Chrome is already warm, the CDP connection is already open, and
    the session is already created.

    **Ordering contract**: callers MUST ``await reap_stale_daemon()``
    before spawning ``preheat`` so we never talk to a half-dead
    predecessor.  The Mustang lifespan enforces this by running the
    reap synchronously during startup and only then scheduling
    ``preheat`` as a background task.

    Best-effort: if agent-browser isn't installed we no-op silently;
    if the preheat call itself fails we just log.  Never raises.
    """
    if not is_available():
        logger.debug("agent-browser preheat skipped — CLI not available")
        return
    try:
        result = await run_with_timeout(
            [str(AGENT_BROWSER_CLI), "open", "about:blank"],
            cwd="/",
            timeout_s=_PREHEAT_TIMEOUT_S,
            env=env(),
        )
        if result.timed_out:
            logger.warning(
                "agent-browser preheat timed out after %ss — "
                "Chrome cold start is abnormally slow; first page_fetch "
                "call may still time out",
                _PREHEAT_TIMEOUT_S,
            )
        elif result.returncode != 0:
            logger.warning(
                "agent-browser preheat exited %s: %s",
                result.returncode,
                (result.stderr or result.stdout).strip()[:500],
            )
        else:
            logger.debug("agent-browser preheat ok")
    except Exception:
        logger.debug("agent-browser preheat failed (best-effort)", exc_info=True)


# Register cleanup at module import time.  Both page_fetch.py and
# browser.py import from this module, but the registration only
# happens once because Python caches modules on first import.
register_cleanup(_close_browser_on_shutdown)


__all__ = [
    "AGENT_BROWSER_CLI",
    "env",
    "install_hint",
    "is_available",
    "preheat",
    "reap_stale_daemon",
]
