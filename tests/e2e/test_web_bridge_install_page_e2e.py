"""E2E probe for WebBridge install/status URL and fake-extension pairing."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
import websockets

from probe.client import ProbeClient

pytestmark = pytest.mark.e2e


def _run(coro: Any) -> Any:
    return asyncio.run(asyncio.wait_for(coro, timeout=30))


def test_web_bridge_install_url_and_fake_extension_pairing(kernel: tuple[int, str]) -> None:
    port, token = kernel

    async def _probe() -> None:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            status = await client._request("_mustang.agent/web_bridge/pair_start", {})
            assert status["status"] == "setup_needed"
            assert status["pairingToken"]
            assert status["bridgeWsUrl"].startswith("ws://127.0.0.1:")
            assert status["installUrl"] == f"http://127.0.0.1:{port}/web-bridge/install"
            assert status["unpackedPath"].endswith("src/web-bridge-extension/dist/chrome")

            async with httpx.AsyncClient(timeout=10) as http:
                html = await http.get(status["installUrl"])
                assert html.status_code == 200
                assert "DeepCLI WebBridge" in html.text
                archive = await http.get(f"http://127.0.0.1:{port}/web-bridge/deepcli-web-bridge.zip")
                assert archive.status_code == 200
                assert archive.headers["content-type"].startswith("application/zip")
                current = await http.get(f"http://127.0.0.1:{port}/web-bridge/status.json")
                assert current.status_code == 200
                status = current.json()

            async with websockets.connect(status["bridgeWsUrl"]) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "protocolVersion": "web-bridge.v1",
                            "extensionId": "fake-e2e-extension",
                            "pairingToken": status["pairingToken"],
                            "browser": {"name": "Chrome", "version": "e2e"},
                        }
                    )
                )
                ack = json.loads(await ws.recv())
                assert ack["ok"] is True
                assert ack["secret"]

                paired = await client._request("_mustang.agent/web_bridge/status", {})
                assert paired["status"] == "available"
                assert paired["paired"] is True
                assert paired["connected"] is True
                assert paired["browser"]["name"] == "Chrome"

            await client._request("_mustang.agent/web_bridge/pair_reset", {})

    _run(_probe())
