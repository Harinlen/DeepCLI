"""WebBridge-backed WebFetch backend."""

from __future__ import annotations

from typing import Any

import httpx

from kernel.agents.mustang.tools.web.fetch_backends.base import FetchBackend, FetchResult
from kernel.agents.mustang.tools.web.web_bridge.protocol import WebBridgeFetchResult

_CLIENT = None
_MANAGER = None


def set_web_bridge_manager(manager: object | None) -> None:
    """Set the WebBridge client used by this backend.

    The historical name is kept as a compatibility alias for tests and older
    ToolManager wiring.  New production wiring passes an AccessAgent client,
    not a process-local WebBridge server.
    """

    global _CLIENT, _MANAGER
    _CLIENT = manager
    _MANAGER = manager


def _web_bridge_client() -> object | None:
    return _CLIENT or _MANAGER


class AccessAgentWebBridgeClient:
    """WebBridge facade that calls the AccessAgent-owned WebBridge edge."""

    def __init__(self, access_url: str) -> None:
        self._access_url = access_url.rstrip("/")

    def status(self) -> dict[str, Any]:
        """Return WebBridge status through the AccessAgent HTTP edge."""

        try:
            with httpx.Client(timeout=5) as client:
                response = client.get(f"{self._access_url}/web-bridge/status.json")
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
        except Exception as exc:
            return {
                "status": "unavailable",
                "paired": False,
                "connected": False,
                "message": str(exc),
            }

    def pair_start(self) -> dict[str, Any]:
        """Start WebBridge pairing through the AccessAgent HTTP edge."""

        with httpx.Client(timeout=10) as client:
            response = client.post(f"{self._access_url}/web-bridge/pair")
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {}

    async def pair_reset(self) -> dict[str, Any]:
        """Reset WebBridge pairing through the AccessAgent HTTP edge."""

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{self._access_url}/web-bridge/reset")
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {}

    async def fetch_tab(self, url: str, *, max_chars: int = 50_000) -> WebBridgeFetchResult:
        """Ask AccessAgent/resource:web_bridge to fetch one tab."""

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self._access_url}/web-bridge/fetch",
                json={"url": url, "maxChars": max_chars},
            )
            response.raise_for_status()
            return WebBridgeFetchResult.model_validate(response.json())


class BrowserFetchBackend(FetchBackend):
    """Fetch pages through the user's paired browser extension."""

    name = "browser"

    def is_available(self) -> bool:
        client = _web_bridge_client()
        return bool(client is not None and client.status().get("connected"))  # type: ignore[attr-defined]

    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        client = _web_bridge_client()
        if client is None:
            return FetchResult(url=url, content="", content_type="", error="WebBridge is not running")
        try:
            response = await client.fetch_tab(url, max_chars=max_chars)  # type: ignore[attr-defined]
        except Exception as exc:
            return FetchResult(url=url, content="", content_type="", error=str(exc))
        if not response.ok:
            return FetchResult(
                url=response.final_url or response.url or url,
                content="",
                content_type="text/html; backend=browser",
                status_code=403 if response.error == "permission_denied" else 502,
                error=response.message or response.error or "WebBridge fetch failed",
                metadata={
                    "browser_signals": response.signals.model_dump(),
                    "extraction_method": response.extraction_method,
                },
            )
        content = response.readability_text or response.text or _metadata_summary(response.metadata)
        raw_length = len(content)
        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars]
        return FetchResult(
            url=response.final_url or response.url or url,
            content=content,
            content_type="text/html; backend=browser",
            title=response.title,
            status_code=200,
            truncated=truncated,
            raw_length=raw_length,
            metadata={
                "browser_signals": response.signals.model_dump(),
                "extraction_method": response.extraction_method,
            },
        )


def _metadata_summary(metadata: dict[str, object]) -> str:
    parts = []
    for key in ("description", "siteName", "site_name"):
        value = metadata.get(key)
        if value:
            parts.append(str(value))
    return "\n".join(parts)


__all__ = ["AccessAgentWebBridgeClient", "BrowserFetchBackend", "set_web_bridge_manager"]
