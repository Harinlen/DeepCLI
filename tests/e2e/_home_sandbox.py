"""Sandboxed ``$HOME`` for e2e kernel subprocesses.

Every e2e test starts a real kernel subprocess whose state (SQLite
databases, session journals, memory files, flags, plans, etc.) lives
under ``Path.home() / ".mustang" / ...``.  Previously the test kernel
shared the developer's real ``~/.mustang/`` — which meant cron tasks,
memory entries, and sessions created by tests polluted the developer's
environment and kept firing long after the tests finished.

This module gives each e2e fixture a disposable ``$HOME`` under
``/tmp``.  The kernel subprocess receives that path in its environment;
``Path.home()`` inside the subprocess resolves to it; all kernel state
lands in the sandbox and is wiped on teardown.

Usage::

    from tests.e2e._home_sandbox import prepare_test_home, token_path_for

    home = prepare_test_home("kernel")          # fresh sandbox
    env = {**os.environ, "HOME": str(home)}
    proc = subprocess.Popen([...], env=env)
    ...
    token = token_path_for(home).read_text().strip()
    ...
    cleanup_test_home(home)                     # wipe sandbox

The real user config (``~/.mustang/config/kernel.yaml``) is partially
mirrored into the sandbox — only the ``llm:`` section, so LLM-dependent
tests run against the configured provider.  ``gateways:``, ``mcp.yaml``,
``secrets.db``, and all persistent state are intentionally excluded:
the test kernel must never act as the real Discord bot or inherit
user-specific OAuth tokens.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import yaml


def _sandbox_root(label: str) -> Path:
    """Fixed per-label path so leftovers from a crashed run are found
    and wiped on the next startup."""
    return Path(tempfile.gettempdir()) / f"mustang-e2e-{label}"


def prepare_test_home(label: str) -> Path:
    """Create a fresh sandbox HOME, returning its absolute path.

    Steps:
    1. Remove any leftover sandbox from a prior run.
    2. Create ``<sandbox>/.mustang/config/``.
    3. If the developer has a real ``~/.mustang/config/kernel.yaml``,
       copy only its ``llm:`` section into the sandbox so LLM tests
       can reach the configured provider.  Anything else is omitted.

    Args:
        label: Short identifier for the fixture (e.g., ``"kernel"``,
            ``"mcp"``, ``"repl"``).  Used to disambiguate sandbox
            paths when multiple kernels run concurrently in one
            test session.
    """
    home = _sandbox_root(label)
    if home.exists():
        shutil.rmtree(home)
    config_dir = home / ".mustang" / "config"
    config_dir.mkdir(parents=True)

    real_cfg = Path.home() / ".mustang" / "config" / "kernel.yaml"
    if real_cfg.exists():
        try:
            parsed = yaml.safe_load(real_cfg.read_text()) or {}
        except yaml.YAMLError:
            parsed = {}
        llm_section = parsed.get("llm")
        if llm_section:
            (config_dir / "kernel.yaml").write_text(
                yaml.safe_dump({"llm": llm_section}, sort_keys=False),
            )

    # flags.yaml must set transport.stack=acp; without it the kernel
    # defaults to "dummy" and ACP handshakes never respond.
    # Write the minimum required flags rather than copying the user's real
    # flags.yaml, which may contain user-specific settings (e.g.
    # tools.repl=true) that would interfere with tests that assume
    # the default tool set.  Fixtures that need additional flags
    # (e.g. repl_kernel) override via MUSTANG_FLAGS_PATH env var.
    (home / ".mustang" / "flags.yaml").write_text(
        "transport:\n  stack: acp\n"
    )

    return home


def cleanup_test_home(home: Path) -> None:
    """Remove the sandbox on teardown.  Best-effort — never raises."""
    shutil.rmtree(home, ignore_errors=True)


def token_path_for(home: Path) -> Path:
    """Path where the kernel writes its auth token inside *home*."""
    return home / ".mustang" / "state" / "auth_token"
