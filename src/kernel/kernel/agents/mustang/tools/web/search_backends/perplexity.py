"""Perplexity search backend — POST api.perplexity.ai/chat/completions."""

from __future__ import annotations

import os

import httpx

from kernel.agents.mustang.tools.web.search_backends.base import SearchBackend, SearchResult


class PerplexitySearchBackend(SearchBackend):
    """Perplexity sonar — answer synthesis with citations."""

    name = "perplexity"

    def is_available(self) -> bool:
        return bool(os.getenv("PERPLEXITY_API_KEY", "").strip())

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        api_key = os.getenv("PERPLEXITY_API_KEY", "")
        # pplx- prefix = direct Perplexity; otherwise OpenRouter
        if api_key.startswith("pplx-"):
            base_url = "https://api.perplexity.ai"
        else:
            base_url = "https://openrouter.ai/api/v1"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "perplexity/sonar-pro",
                    "messages": [{"role": "user", "content": query}],
                },
            )
            resp.raise_for_status()

        data = resp.json()
        citations = data.get("citations", [])

        if citations:
            return [SearchResult(title="", url=c, snippet="") for c in citations[:limit]]
        # No structured citations — return the answer as a single result
        choices = data.get("choices") or [{}]
        content = choices[0].get("message", {}).get("content", "")
        return (
            [
                SearchResult(
                    title="Perplexity answer",
                    url="",
                    snippet=content[:500],
                )
            ]
            if content
            else []
        )


__all__ = ["PerplexitySearchBackend"]
