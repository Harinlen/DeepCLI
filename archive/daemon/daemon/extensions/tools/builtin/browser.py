"""BrowserTool — full session control for advanced web interaction.

Backed by ``agent-browser`` (Chrome via CDP).  Single tool with an
``action`` discriminator covering five subactions:

==========  ============================================================
Action      What it does
==========  ============================================================
open        Navigate to a URL
page        Return the current page's accessibility tree (text)
snapshot    Take a screenshot of the current page (PNG, returned as
            ``image_parts``)
network     List captured network requests (XHR / fetch / etc.)
close       Close the browser session
==========  ============================================================

Naming note: agent-browser's CLI calls the accessibility-tree text
"snapshot" and the PNG "screenshot".  We rename for intuition:
``browser snapshot`` = take a picture, ``browser page`` = read the
text contents.

For one-shot reads of a single page, prefer ``page_fetch`` (no need
to manually open / close).  For JSON APIs, use ``http_fetch``.
"""

from __future__ import annotations

import base64
import logging
import tempfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from daemon.extensions.tools.base import (
    ConcurrencyHint,
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.extensions.tools.builtin import agent_browser_cli
from daemon.extensions.tools.builtin.subprocess_utils import run_with_timeout
from daemon.extensions.tools.domain_filter import check_domain
from daemon.providers.base import ImageContent

logger = logging.getLogger(__name__)

_TIMEOUT_S = 30.0


class BrowserTool(Tool):
    """Full browser session control via agent-browser."""

    name = "browser"
    description = (
        "Full browser session control for advanced web interaction. "
        "Backed by agent-browser (Chrome via CDP). Use this for multi-step "
        "browsing, screenshots, or capturing network traffic.\n\n"
        "Subactions (passed as the `action` parameter):\n"
        "  - open       Navigate to a URL (also supply `url`)\n"
        "  - page       Return the current page's accessibility tree text\n"
        "  - snapshot   Take a screenshot of the current page (PNG)\n"
        "  - network    List captured network requests (XHR / fetch / etc.)\n"
        "  - close      Close the browser session\n\n"
        "Typical workflows:\n"
        "  - Multi-page navigation: open → page → open → page → close\n"
        "  - Screenshot a page:     open → snapshot → close\n"
        "  - Capture API calls:     open → network → close\n\n"
        "**For one-shot reads of a single page**, prefer `page_fetch` — it's "
        "simpler. **For JSON APIs / REST endpoints**, use `http_fetch` — it's "
        "faster and doesn't need Chrome."
    )
    permission_level = PermissionLevel.PROMPT
    concurrency = ConcurrencyHint.SERIAL
    # Screenshots can be huge — disable the default 50k char budget
    # so the base64 image_parts payload survives.
    max_result_chars: int | None = None

    class Input(BaseModel):
        action: Literal["open", "page", "snapshot", "network", "close"] = Field(
            description="Subaction to perform.",
        )
        url: str | None = Field(
            default=None,
            description="URL for the `open` action (HTTP or HTTPS).",
        )

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        validated = self.Input.model_validate(params)

        if not agent_browser_cli.is_available():
            return ToolResult(
                output=agent_browser_cli.install_hint(),
                is_error=True,
            )

        cli = str(agent_browser_cli.AGENT_BROWSER_CLI)
        env = agent_browser_cli.env()

        action = validated.action

        if action == "open":
            return await self._do_open(cli, env, ctx, validated.url)
        if action == "page":
            return await self._do_page(cli, env, ctx)
        if action == "snapshot":
            return await self._do_snapshot(cli, env, ctx)
        if action == "network":
            return await self._do_network(cli, env, ctx)
        if action == "close":
            return await self._do_close(cli, env, ctx)
        # Unreachable: Literal validation guards this.
        return ToolResult(output=f"Unknown action: {action}", is_error=True)

    # ── Action handlers ──────────────────────────────────────

    async def _do_open(
        self,
        cli: str,
        env: dict[str, str],
        ctx: ToolContext,
        url: str | None,
    ) -> ToolResult:
        if not url:
            return ToolResult(
                output="The `open` action requires a `url` parameter.",
                is_error=True,
            )
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return ToolResult(
                output=f"Rejected: only http(s) URLs allowed, got {parsed.scheme!r}",
                is_error=True,
            )
        if not parsed.netloc:
            return ToolResult(output="Rejected: URL missing host", is_error=True)
        if domain_err := check_domain(url):
            return ToolResult(output=domain_err, is_error=True)

        result = await self._run([cli, "open", url], cwd=ctx.cwd, env=env)
        if result is None:
            return ToolResult(output="Failed to launch agent-browser", is_error=True)
        return self._wrap_subprocess_result(result, success_message=f"Opened {url}")

    async def _do_page(
        self,
        cli: str,
        env: dict[str, str],
        ctx: ToolContext,
    ) -> ToolResult:
        result = await self._run([cli, "snapshot", "-i"], cwd=ctx.cwd, env=env)
        if result is None:
            return ToolResult(output="Failed to launch agent-browser", is_error=True)
        if result.timed_out or result.returncode != 0:
            return self._wrap_subprocess_result(result, success_message="")
        return ToolResult(output=result.stdout or "(empty page)")

    async def _do_snapshot(
        self,
        cli: str,
        env: dict[str, str],
        ctx: ToolContext,
    ) -> ToolResult:
        # Write to a temp PNG, capture it as image_parts.
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            result = await self._run(
                [cli, "screenshot", str(tmp_path)],
                cwd=ctx.cwd,
                env=env,
            )
            if result is None:
                return ToolResult(output="Failed to launch agent-browser", is_error=True)
            if result.timed_out or result.returncode != 0:
                return self._wrap_subprocess_result(result, success_message="")

            if not tmp_path.exists() or tmp_path.stat().st_size == 0:
                return ToolResult(
                    output="agent-browser screenshot produced no output",
                    is_error=True,
                )

            image_bytes = tmp_path.read_bytes()
            b64 = base64.b64encode(image_bytes).decode("ascii")
            image = ImageContent(
                media_type="image/png",
                data_base64=b64,
            )
            return ToolResult(
                output=f"Screenshot captured ({len(image_bytes)} bytes).",
                image_parts=[image],
            )
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    async def _do_network(
        self,
        cli: str,
        env: dict[str, str],
        ctx: ToolContext,
    ) -> ToolResult:
        result = await self._run(
            [cli, "network", "requests"],
            cwd=ctx.cwd,
            env=env,
        )
        if result is None:
            return ToolResult(output="Failed to launch agent-browser", is_error=True)
        if result.timed_out or result.returncode != 0:
            return self._wrap_subprocess_result(result, success_message="")
        return ToolResult(output=result.stdout or "(no network requests captured)")

    async def _do_close(
        self,
        cli: str,
        env: dict[str, str],
        ctx: ToolContext,
    ) -> ToolResult:
        result = await self._run([cli, "close"], cwd=ctx.cwd, env=env)
        if result is None:
            return ToolResult(output="Failed to launch agent-browser", is_error=True)
        return self._wrap_subprocess_result(result, success_message="Closed browser session.")

    # ── Helpers ──────────────────────────────────────────────

    async def _run(
        self,
        argv: list[str],
        *,
        cwd: str,
        env: dict[str, str],
    ):
        """Run an agent-browser subprocess. Returns SubprocessResult or None on OSError."""
        try:
            return await run_with_timeout(argv, cwd=cwd, timeout_s=_TIMEOUT_S, env=env)
        except OSError as exc:
            logger.warning("Failed to launch agent-browser: %s", exc)
            return None

    def _wrap_subprocess_result(self, result, *, success_message: str) -> ToolResult:
        """Convert a SubprocessResult into a ToolResult."""
        if result.timed_out:
            return ToolResult(
                output=f"agent-browser timed out after {_TIMEOUT_S:.0f}s",
                is_error=True,
            )
        if result.returncode != 0:
            return ToolResult(
                output=(
                    f"agent-browser failed (exit {result.returncode}):\n"
                    f"{result.stderr or result.stdout}"
                ),
                is_error=True,
            )
        return ToolResult(output=success_message or result.stdout)


__all__ = ["BrowserTool"]
