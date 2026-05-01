"""Kimi/Moonshot search backend — POST api.moonshot.ai/v1/chat/completions."""

from __future__ import annotations

import os

import httpx

from kernel.tools.web.search_backends.base import SearchBackend, SearchResult


class KimiSearchBackend(SearchBackend):
    """Kimi/Moonshot LLM with built-in $web_search tool."""

    name = "kimi"

    def is_available(self) -> bool:
        return bool(
            os.getenv("KIMI_API_KEY", "").strip() or os.getenv("MOONSHOT_API_KEY", "").strip()
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        api_key = os.getenv("KIMI_API_KEY", "").strip() or os.getenv("MOONSHOT_API_KEY", "").strip()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.moonshot.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "moonshot-v1-128k",
                    "messages": [{"role": "user", "content": query}],
                    "tools": [
                        {
                            "type": "builtin_function",
                            "function": {"name": "$web_search"},
                        }
                    ],
                },
            )
            resp.raise_for_status()

        data = resp.json()
        search_results = data.get("search_results", [])
        if search_results:
            return [
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("snippet", ""),
                )
                for r in search_results[:limit]
            ]
        # Fallback: extract from message content
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return (
            [
                SearchResult(
                    title="Kimi answer",
                    url="",
                    snippet=content[:500],
                )
            ]
            if content
            else []
        )


__all__ = ["KimiSearchBackend"]
