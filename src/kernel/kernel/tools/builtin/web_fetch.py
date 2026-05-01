"""WebFetchTool — fetch URL content with multi-backend fallback."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from fnmatch import fnmatch
from typing import Any, ClassVar
from urllib.parse import urlparse

from kernel.orchestrator.types import ToolKind
from kernel.tools.tool import Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallResult,
    ToolInputError,
)

from kernel.tools.web.domain_filter import check_domain
from kernel.tools.web.preapproved import PREAPPROVED_HOSTS


class WebFetchTool(Tool[dict[str, Any], dict[str, Any]]):
    """Fetch a URL and return its content as text/Markdown."""

    name: ClassVar[str] = "WebFetch"
    description_key: ClassVar[str] = "tools/web_fetch"
    description: ClassVar[str] = "Fetch a URL and return its content."
    kind: ClassVar[ToolKind] = ToolKind.read
    should_defer: ClassVar[bool] = True
    search_hint: ClassVar[str] = "fetch web page URL content download"
    interrupt_behavior: ClassVar[str] = "cancel"  # type: ignore[assignment]
    max_result_size_chars: ClassVar[int] = 100_000

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "HTTP or HTTPS URL to fetch.",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "What to extract from the page. Guides content "
                    "selection when the backend supports it."
                ),
            },
            "max_chars": {
                "type": "integer",
                "default": 50_000,
                "description": "Maximum characters of content to return.",
            },
        },
        "required": ["url"],
    }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    async def validate_input(self, input: dict[str, Any], ctx: Any) -> None:
        url = input.get("url", "")
        if not url:
            raise ToolInputError("url is required")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https", ""}:
            raise ToolInputError(f"Only http(s) URLs are allowed, got {parsed.scheme!r}")
        if err := check_domain(url):
            raise ToolInputError(err)

    # ------------------------------------------------------------------
    # Permission
    # ------------------------------------------------------------------

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        host = urlparse(input.get("url", "")).hostname or ""
        if host in PREAPPROVED_HOSTS:
            return PermissionSuggestion(
                risk="low",
                default_decision="allow",
                reason=f"preapproved host: {host}",
            )
        return PermissionSuggestion(
            risk="medium",
            default_decision="ask",
            reason=f"outbound fetch to {host}",
        )

    def prepare_permission_matcher(self, input: dict[str, Any]) -> Any:
        host = urlparse(input.get("url", "")).hostname or ""
        return lambda pattern: fnmatch(host, pattern)

    def activity_description(self, input: dict[str, Any]) -> str | None:
        url = input.get("url", "")
        host = urlparse(url).hostname or url[:40]
        return f"Fetching {host}"

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def call(
        self,
        input: dict[str, Any],
        ctx: Any,
    ) -> AsyncGenerator:
        from kernel.tools.web.fetch_backends import fetch_with_fallback
        from kernel.protocol.interfaces.contracts.content_block import TextBlock

        url = input["url"]
        user_prompt = input.get("prompt")
        max_chars = input.get("max_chars", 50_000)
        preferred = os.getenv("MUSTANG_FETCH_BACKEND")

        result, backend_name = await fetch_with_fallback(
            url,
            max_chars=max_chars,
            preferred=preferred,
        )

        # Format header + raw content.
        parts: list[str] = []
        if result.url != url:
            parts.append(f"[fetched: {url} → {result.url} (via {backend_name})]")
        else:
            parts.append(f"[fetched: {url} (via {backend_name})]")

        post_processed = False
        if result.error:
            parts.append(f"Error: {result.error}")
        else:
            if result.content_type:
                parts.append(f"Content-Type: {result.content_type}")
            parts.append("")

            # Secondary-model post-processing (CC parity): if the LLM
            # supplied a ``prompt`` and the session wired a summarisation
            # closure, run the content through the compact-role model
            # with the CC-style wrapper prompt.  Falls back to raw
            # content when no summariser is available.
            summarise = getattr(ctx, "summarise", None)
            if user_prompt and summarise is not None and not result.error:
                try:
                    wrapped = _make_secondary_model_prompt(
                        result.content,
                        user_prompt,
                        is_preapproved=(urlparse(result.url).hostname or "") in PREAPPROVED_HOSTS,
                    )
                    summary = await summarise(wrapped, user_prompt)
                    if isinstance(summary, str) and summary.strip():
                        parts.append(summary)
                        post_processed = True
                except Exception:
                    # Any provider error falls through to raw content.
                    post_processed = False

            if not post_processed:
                parts.append(result.content)
                if len(result.content) >= max_chars:
                    parts.append(f"\n... (truncated at {max_chars} chars)")

        output_text = "\n".join(parts)

        yield ToolCallResult(
            data={
                "url": result.url,
                "backend": backend_name,
                "status_code": result.status_code,
                "error": result.error,
                "post_processed": post_processed,
            },
            llm_content=[TextBlock(text=output_text)],
            display=TextDisplay(text=output_text),
        )


def _make_secondary_model_prompt(
    markdown_content: str,
    prompt: str,
    is_preapproved: bool,
) -> str:
    """CC's ``makeSecondaryModelPrompt`` (WebFetchTool/prompt.ts:23-45) —
    wraps page content + user prompt with appropriate guidelines based
    on whether the host is on the preapproved list."""
    if is_preapproved:
        guidelines = (
            "Provide a concise response based on the content above. "
            "Include relevant details, code examples, and documentation "
            "excerpts as needed."
        )
    else:
        guidelines = (
            "Provide a concise response based only on the content above. "
            "In your response:\n"
            " - Enforce a strict 125-character maximum for quotes from "
            "any source document. Open Source Software is ok as long as "
            "we respect the license.\n"
            " - Use quotation marks for exact language from articles; "
            "any language outside of the quotation should never be "
            "word-for-word the same.\n"
            " - You are not a lawyer and never comment on the legality "
            "of your own prompts and responses.\n"
            " - Never produce or reproduce exact song lyrics."
        )
    return (
        "Web page content:\n"
        "---\n"
        f"{markdown_content}\n"
        "---\n\n"
        f"{prompt}\n\n"
        f"{guidelines}\n"
    )


__all__ = ["WebFetchTool"]
