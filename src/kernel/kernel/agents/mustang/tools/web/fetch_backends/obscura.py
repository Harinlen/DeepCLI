"""Obscura WebFetch backend."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil

from kernel.agents.mustang.tools.web.domain_filter import check_domain
from kernel.agents.mustang.tools.web.fetch_backends.base import FetchBackend, FetchResult
from kernel.agents.mustang.tools.web.management import _deepcli_package_dir


def _obscura_binary() -> str | None:
    for env_key in ("MUSTANG_OBSCURA_BIN", "OBSCURA_BIN"):
        value = os.getenv(env_key, "").strip()
        if value and Path(value).is_file():
            return value

    bundled = _deepcli_package_dir("obscura") / "bin" / (
        "obscura.exe" if os.name == "nt" else "obscura"
    )
    if bundled.is_file():
        return str(bundled)

    return shutil.which("obscura")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return max(1, int(value))
    except ValueError:
        return default


async def _run_obscura(command: list[str], *, timeout: int) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", "Obscura fetch timed out"
    return (
        int(proc.returncode or 0),
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


class ObscuraFetchBackend(FetchBackend):
    """Local Rust headless-browser backend powered by Obscura."""

    name = "obscura"

    def is_available(self) -> bool:
        return _obscura_binary() is not None

    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        if err := check_domain(url):
            return FetchResult(url=url, content="", content_type="", error=err)

        binary = _obscura_binary()
        if binary is None:
            return FetchResult(url=url, content="", content_type="", error="Obscura is not installed")

        dump = os.getenv("MUSTANG_OBSCURA_DUMP", "markdown").strip().lower() or "markdown"
        if dump not in {"markdown", "text", "html"}:
            dump = "markdown"
        wait_until = os.getenv("MUSTANG_OBSCURA_WAIT_UNTIL", "domcontentloaded").strip()
        if wait_until not in {"load", "domcontentloaded", "networkidle0"}:
            wait_until = "domcontentloaded"
        navigation_timeout = _env_int("MUSTANG_OBSCURA_TIMEOUT", 30)

        command = [binary]
        proxy = os.getenv("MUSTANG_OBSCURA_PROXY", "").strip()
        if proxy:
            command.extend(["--proxy", proxy])
        command.extend(
            [
                "fetch",
                url,
                "--dump",
                dump,
                "--wait-until",
                wait_until,
                "--timeout",
                str(navigation_timeout),
                "--quiet",
            ]
        )
        if os.getenv("MUSTANG_OBSCURA_STEALTH", "").strip().lower() in {"1", "true", "yes", "on"}:
            command.append("--stealth")

        exit_code, stdout, stderr = await _run_obscura(command, timeout=navigation_timeout + 10)
        if exit_code != 0:
            message = (stderr or stdout or f"Obscura exited with code {exit_code}").strip()
            return FetchResult(url=url, content="", content_type="", status_code=exit_code, error=message)

        content = stdout[:max_chars]
        return FetchResult(
            url=url,
            content=content,
            content_type={
                "markdown": "text/markdown",
                "text": "text/plain",
                "html": "text/html",
            }[dump],
            truncated=len(stdout) > max_chars,
            raw_length=len(stdout),
        )


__all__ = ["ObscuraFetchBackend"]
