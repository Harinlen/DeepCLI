"""xAI/Grok search backend — POST api.x.ai/v1/chat/completions."""

from __future__ import annotations

import os

import httpx

from kernel.tools.web.search_backends.base import SearchBackend, SearchResult


class XaiSearchBackend(SearchBackend):
    """xAI Grok with web search tool."""

    name = "xai"

    def is_available(self) -> bool:
        return bool(os.getenv("XAI_API_KEY", "").strip())

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.x.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {os.getenv('XAI_API_KEY', '')}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "grok-3",
                    "messages": [{"role": "user", "content": query}],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }
                    ],
                },
            )
            resp.raise_for_status()

        data = resp.json()
        # Extract citations if present
        citations = data.get("citations", [])
        if citations:
            return [SearchResult(title="", url=c, snippet="") for c in citations[:limit]]
        # Fallback: message content
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return (
            [
                SearchResult(
                    title="Grok answer",
                    url="",
                    snippet=content[:500],
                )
            ]
            if content
            else []
        )


__all__ = ["XaiSearchBackend"]
