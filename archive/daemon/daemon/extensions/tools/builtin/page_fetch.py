"""PageFetchTool — one-shot read of a web page using a real browser.

Backed by ``agent-browser`` (Chrome via CDP).  Runs ``open <url>`` then
``snapshot -i`` to capture the accessibility-tree text and returns it
as the tool result.  Handles JavaScript-heavy SPAs out of the box.

Use cases: read articles, documentation, blogs, GitHub READMEs,
search results, dashboards, social media — anything human-facing.

For JSON APIs and machine endpoints, use ``http_fetch`` instead (no
browser cold-start).  For multi-step browsing, screenshots, or XHR
capture, use ``browser``.
"""

from __future__ import annotations

import logging
from typing import Any
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

logger = logging.getLogger(__name__)

_TIMEOUT_S = 30.0


class PageFetchTool(Tool):
    """Open a URL in a headless browser and return its accessibility-tree text."""

    name = "page_fetch"
    description = (
        "Fetch the contents of a **web page** using a real headless browser. "
        "Returns the page's accessibility tree as compact text — the main "
        "content, headings, links, and form elements with stable refs.\n\n"
        "## This is the default tool for reading web pages\n\n"
        "**Use page_fetch for any URL a human would open in a browser**, "
        "regardless of whether the page is JavaScript-heavy or server-"
        "rendered. Concrete examples:\n\n"
        "- News articles, blog posts, Substack, Medium\n"
        "- Documentation sites, READMEs, knowledge bases\n"
        "- Wikipedia, Stack Overflow, Reddit threads\n"
        "- GitHub / GitLab / Bitbucket repository pages\n"
        "- Search engine results pages (Google, DuckDuckGo, etc.)\n"
        "- Government / institutional pages (weather, statistics, gov data)\n"
        "- Dashboards, social media profiles, product pages, marketing sites\n"
        "- Any URL ending in `.html`, `.shtml`, `.htm`, or with no extension\n"
        "- Any URL where you don't already know the response is structured "
        "machine data\n\n"
        "page_fetch returns the **structured content** the user wants — "
        "headings, paragraphs, links, lists — instead of raw HTML cluttered "
        "with navigation, ads, and page chrome. Even server-rendered HTML "
        "pages benefit from this.\n\n"
        "Single-shot: opens the URL, captures the page text, and you get the "
        "result. Backed by agent-browser (Chrome via CDP). The first call may "
        "take a few seconds while Chrome cold-starts; subsequent calls are fast.\n\n"
        "## When NOT to use page_fetch\n\n"
        "**Only use `http_fetch` instead when the URL is unambiguously a "
        "machine API endpoint**: hostname starts with `api.`, path starts "
        "with `/api/` or `/v1/` or `/graphql`, the response is documented "
        "as JSON / XML / RSS, or you're hitting a CDN / raw file URL.\n\n"
        "For multi-step browsing, screenshots, or capturing XHR network "
        "traffic, use the `browser` tool instead."
    )
    permission_level = PermissionLevel.PROMPT
    # Browser session is global — multiple page_fetch calls would
    # race on the same Chrome instance.
    concurrency = ConcurrencyHint.SERIAL

    class Input(BaseModel):
        url: str = Field(description="HTTP or HTTPS URL.")
        max_chars: int = Field(
            default=50_000,
            gt=0,
            description="Maximum characters of accessibility-tree text to return.",
        )

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        validated = self.Input.model_validate(params)

        # --- URL validation ---
        parsed = urlparse(validated.url)
        if parsed.scheme not in {"http", "https"}:
            return ToolResult(
                output=f"Rejected: only http(s) URLs allowed, got {parsed.scheme!r}",
                is_error=True,
            )
        if not parsed.netloc:
            return ToolResult(output="Rejected: URL missing host", is_error=True)
        if domain_err := check_domain(validated.url):
            return ToolResult(output=domain_err, is_error=True)

        # --- CLI availability ---
        if not agent_browser_cli.is_available():
            return ToolResult(
                output=agent_browser_cli.install_hint(),
                is_error=True,
            )

        cli = str(agent_browser_cli.AGENT_BROWSER_CLI)
        env = agent_browser_cli.env()

        # --- Step 1: open the URL ---
        try:
            open_result = await run_with_timeout(
                [cli, "open", validated.url],
                cwd=ctx.cwd,
                timeout_s=_TIMEOUT_S,
                env=env,
            )
        except OSError as exc:
            return ToolResult(
                output=f"Failed to launch agent-browser: {exc}",
                is_error=True,
            )

        if open_result.timed_out:
            return ToolResult(
                output=f"agent-browser open timed out after {_TIMEOUT_S:.0f}s on {validated.url}",
                is_error=True,
            )
        if open_result.returncode != 0:
            return ToolResult(
                output=(
                    f"agent-browser open failed (exit {open_result.returncode}):\n"
                    f"{open_result.stderr or open_result.stdout}"
                ),
                is_error=True,
            )

        # --- Step 2: snapshot the page (a11y tree) ---
        try:
            snap_result = await run_with_timeout(
                [cli, "snapshot", "-i"],
                cwd=ctx.cwd,
                timeout_s=_TIMEOUT_S,
                env=env,
            )
        except OSError as exc:
            return ToolResult(
                output=f"Failed to invoke agent-browser snapshot: {exc}",
                is_error=True,
            )

        if snap_result.timed_out:
            return ToolResult(
                output=f"agent-browser snapshot timed out after {_TIMEOUT_S:.0f}s",
                is_error=True,
            )
        if snap_result.returncode != 0:
            return ToolResult(
                output=(
                    f"agent-browser snapshot failed (exit {snap_result.returncode}):\n"
                    f"{snap_result.stderr or snap_result.stdout}"
                ),
                is_error=True,
            )

        text = snap_result.stdout
        if len(text) > validated.max_chars:
            text = text[: validated.max_chars] + f"\n\n... (truncated at {validated.max_chars} chars)"

        return ToolResult(output=text or "(empty page)")


__all__ = ["PageFetchTool"]
