"""Unit tests for the WebBridge runtime and install assets."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import websockets

from kernel.agents.mustang.tools.builtin.web_fetch import WebFetchTool
from kernel.agents.mustang.tools.context import ToolContext
from kernel.agents.mustang.tools.file_state import FileStateCache
from kernel.agents.mustang.tools.web.fetch_backends.browser import BrowserFetchBackend
from kernel.agents.mustang.tools.web.fetch_backends import get_backend_by_name
from kernel.agents.mustang.tools.web.fetch_backends.browser import set_web_bridge_manager
from kernel.agents.mustang.tools.web.web_bridge.install_assets import (
    unpacked_extension_path,
    zip_path,
)
from kernel.agents.mustang.tools.web.web_bridge.manager import WebBridgeManager
from kernel.agents.mustang.tools.web.web_bridge.protocol import PROTOCOL_VERSION


async def test_web_bridge_pairs_and_fetches_through_fake_extension() -> None:
    stored_secret = ""
    persisted: dict[str, str] = {}

    async def persist(extension_id: str, secret: str) -> None:
        nonlocal stored_secret
        stored_secret = secret
        persisted["extension_id"] = extension_id

    manager = WebBridgeManager(
        access_port=8765,
        persist_pairing=persist,
        read_secret=lambda: stored_secret,
    )
    await manager.startup()
    try:
        setup = manager.status()
        assert setup["status"] == "setup_needed"
        assert setup["installUrl"] == "http://127.0.0.1:8765/web-bridge/install"

        pairing = manager.pair_start()
        token = pairing["pairingToken"]
        assert token

        async with websockets.connect(manager.bridge_ws_url) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "protocolVersion": PROTOCOL_VERSION,
                        "extensionId": "fake-extension",
                        "pairingToken": token,
                        "browser": {"name": "Chrome", "version": "test"},
                    }
                )
            )
            ack = json.loads(await ws.recv())
            assert ack["ok"] is True
            assert ack["secret"] == stored_secret
            assert persisted["extension_id"] == "fake-extension"
            assert manager.status()["status"] == "available"

            task = asyncio.create_task(manager.fetch_tab("https://example.test", max_chars=20))
            request = json.loads(await ws.recv())
            assert request["type"] == "fetch_tab"
            assert request["url"] == "https://example.test"
            await ws.send(
                json.dumps(
                    {
                        "type": "fetch_result",
                        "id": request["id"],
                        "ok": True,
                        "url": request["url"],
                        "finalUrl": "https://example.test/final",
                        "title": "Example",
                        "text": "visible text",
                        "readabilityText": "readable text",
                        "metadata": {"description": "desc"},
                        "signals": {"loaded": True, "textLength": 12},
                        "extractionMethod": "fake",
                    }
                )
            )
            result = await task
            assert result.ok is True
            assert result.final_url == "https://example.test/final"
            assert result.readability_text == "readable text"
            assert result.signals.text_length == 12
    finally:
        await manager.shutdown()


async def test_browser_backend_returns_browser_signals(monkeypatch: Any) -> None:
    from kernel.agents.mustang.tools.web.fetch_backends import browser as browser_module

    class FakeSignals:
        def model_dump(self) -> dict[str, object]:
            return {"loaded": True, "text_length": 42}

    class FakeResponse:
        ok = True
        final_url = "https://example.test/final"
        url = "https://example.test"
        readability_text = "hello from browser"
        text = ""
        metadata: dict[str, object] = {}
        title = "Browser Page"
        signals = FakeSignals()
        extraction_method = "fake"

    class FakeManager:
        def status(self) -> dict[str, object]:
            return {"connected": True}

        async def fetch_tab(self, url: str, *, max_chars: int = 50_000) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(browser_module, "_MANAGER", FakeManager())
    result = await BrowserFetchBackend().fetch("https://example.test")

    assert result.content == "hello from browser"
    assert result.metadata == {
        "browser_signals": {"loaded": True, "text_length": 42},
        "extraction_method": "fake",
    }


def test_web_bridge_zip_contains_chrome_extension_dist() -> None:
    assert (unpacked_extension_path() / "manifest.json").exists()
    archive = zip_path()
    assert archive.exists()
    assert archive.stat().st_size > 0
    assert Path(archive).name == "deepcli-web-bridge.zip"


async def test_web_fetch_tool_uses_browser_backend_over_web_bridge(tmp_path: Path) -> None:
    stored_secret = ""

    async def persist(_extension_id: str, secret: str) -> None:
        nonlocal stored_secret
        stored_secret = secret

    manager = WebBridgeManager(
        access_port=8765,
        persist_pairing=persist,
        read_secret=lambda: stored_secret,
    )
    await manager.startup()
    set_web_bridge_manager(manager)
    try:
        token = manager.pair_start()["pairingToken"]
        async with websockets.connect(manager.bridge_ws_url) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "protocolVersion": PROTOCOL_VERSION,
                        "extensionId": "fake-tool-extension",
                        "pairingToken": token,
                        "browser": {"name": "Chrome", "version": "tool-test"},
                    }
                )
            )
            ack = json.loads(await ws.recv())
            assert ack["ok"] is True

            async def serve_fetch() -> None:
                request = json.loads(await ws.recv())
                assert request["type"] == "fetch_tab"
                body = (
                    "Weather from browser bridge. "
                    "This body is intentionally long enough to pass WebFetch "
                    "empty-page heuristics while still being deterministic."
                )
                await ws.send(
                    json.dumps(
                        {
                            "type": "fetch_result",
                            "id": request["id"],
                            "ok": True,
                            "url": request["url"],
                            "finalUrl": "https://example.test/weather",
                            "title": "Weather",
                            "text": body,
                            "readabilityText": body,
                            "metadata": {},
                            "signals": {"loaded": True, "textLength": len(body)},
                            "extractionMethod": "fake-extension",
                        }
                    )
                )

            server_task = asyncio.create_task(serve_fetch())
            ctx = ToolContext(
                session_id="web-bridge-tool",
                agent_depth=0,
                agent_id=None,
                cwd=tmp_path,
                cancel_event=asyncio.Event(),
                file_state=FileStateCache(),
            )
            events = []
            async for event in WebFetchTool().call(
                {"url": "https://example.test/weather", "backend": "browser"},
                ctx,
            ):
                events.append(event)
            await server_task

        assert get_backend_by_name("browser") is not None
        assert len(events) == 1
        result = events[0]
        assert result.data["backend"] == "browser"
        assert result.data["url"] == "https://example.test/weather"
        assert result.data["browser_signals"]["text_length"] > 50
        assert "Weather from browser bridge" in result.llm_content[0].text
    finally:
        set_web_bridge_manager(None)
        await manager.shutdown()
