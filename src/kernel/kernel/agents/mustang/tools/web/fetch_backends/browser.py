"""WebBridge-backed WebFetch backend."""

from __future__ import annotations

from kernel.agents.mustang.tools.web.fetch_backends.base import FetchBackend, FetchResult

_MANAGER = None


def set_web_bridge_manager(manager: object | None) -> None:
    """Set the process-local WebBridge manager used by this backend."""

    global _MANAGER
    _MANAGER = manager


class BrowserFetchBackend(FetchBackend):
    """Fetch pages through the user's paired browser extension."""

    name = "browser"

    def is_available(self) -> bool:
        manager = _MANAGER
        return bool(manager is not None and manager.status().get("connected"))  # type: ignore[attr-defined]

    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        manager = _MANAGER
        if manager is None:
            return FetchResult(url=url, content="", content_type="", error="WebBridge is not running")
        try:
            response = await manager.fetch_tab(url, max_chars=max_chars)  # type: ignore[attr-defined]
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


__all__ = ["BrowserFetchBackend", "set_web_bridge_manager"]
