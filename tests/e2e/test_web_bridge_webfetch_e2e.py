"""E2E probe for WebFetch using the WebBridge browser backend."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest
import websockets

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ProbeClient,
    ToolCallEvent,
    ToolCallUpdate,
    TurnComplete,
)

pytestmark = pytest.mark.e2e


_LLM_TIMEOUT = 180.0


def _run(coro: Any, *, timeout: float = _LLM_TIMEOUT) -> Any:
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


def _skip_if_no_llm(port: int, token: str) -> None:
    async def _check() -> list[dict[str, Any]]:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            result = await client._request("_mustang.agent/model/provider_list", {})
        return result.get("providers", [])

    providers = _run(_check(), timeout=30)
    if not providers:
        pytest.skip("No LLM providers configured — skipping")


async def _serve_fake_extension(ws: Any, fetch_requests: list[dict[str, Any]]) -> None:
    async for raw in ws:
        request = json.loads(raw)
        if request.get("type") != "fetch_tab":
            continue
        fetch_requests.append(request)
        body = (
            "Weather from the WebBridge browser backend. "
            "This deterministic E2E body proves the live WebFetch tool "
            "reached the paired browser backend instead of another backend."
        )
        await ws.send(
            json.dumps(
                {
                    "type": "fetch_result",
                    "id": request["id"],
                    "ok": True,
                    "url": request["url"],
                    "finalUrl": "https://example.test/webbridge-weather",
                    "title": "WebBridge Weather",
                    "text": body,
                    "readabilityText": body,
                    "metadata": {"description": "deterministic web bridge result"},
                    "signals": {"loaded": True, "textLength": len(body)},
                    "extractionMethod": "fake-e2e-extension",
                }
            )
        )


def test_webfetch_browser_backend_reaches_fake_extension(kernel: tuple[int, str]) -> None:
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _probe() -> None:
        async with ProbeClient(port=port, token=token, request_timeout=_LLM_TIMEOUT) as client:
            await client.initialize()
            status = await client._request("_mustang.agent/web_bridge/pair_start", {})

            fetch_requests: list[dict[str, Any]] = []
            async with websockets.connect(status["bridgeWsUrl"]) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "protocolVersion": "web-bridge.v1",
                            "extensionId": "fake-webfetch-extension",
                            "pairingToken": status["pairingToken"],
                            "browser": {"name": "Chrome", "version": "e2e-webfetch"},
                        }
                    )
                )
                ack = json.loads(await ws.recv())
                assert ack["ok"] is True
                serve_task = asyncio.create_task(
                    _serve_fake_extension(ws, fetch_requests)
                )
                try:
                    selected = await client._request(
                        "_mustang.agent/web_fetch/set_backend",
                        {"backend": "browser"},
                    )
                    assert selected["backend"] == "browser"
                    assert selected["setupRequired"] is False

                    sid = await client.new_session()
                    text_parts: list[str] = []
                    tool_titles: list[str] = []
                    tool_updates: list[ToolCallUpdate] = []
                    stop_reason = "unknown"
                    prompt = (
                        "Use the WebFetch tool exactly once to fetch "
                        "https://example.test/webbridge-weather. Do not use WebSearch. "
                        "The configured WebFetch backend is browser. After the tool returns, "
                        "reply with the exact phrase 'Weather from the WebBridge browser backend'."
                    )
                    async for event in client.prompt(sid, prompt, timeout=_LLM_TIMEOUT):
                        if isinstance(event, AgentChunk):
                            text_parts.append(event.text)
                        elif isinstance(event, ToolCallEvent):
                            tool_titles.append(event.title)
                        elif isinstance(event, ToolCallUpdate):
                            tool_updates.append(event)
                        elif isinstance(event, PermissionRequest):
                            await client.reply_permission(event.req_id, "allow_once")
                        elif isinstance(event, TurnComplete):
                            stop_reason = event.stop_reason

                    assert stop_reason == "end_turn"
                    assert "WebFetch" in tool_titles
                    assert fetch_requests, "WebFetch did not reach the fake browser extension"
                    assert any(
                        update.meta
                        and update.meta.get("mustang.agent/toolBackend", {}).get("backend")
                        == "browser"
                        for update in tool_updates
                    )
                    assert "Weather from the WebBridge browser backend" in "".join(text_parts)
                finally:
                    serve_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await serve_task
                    await client._request("_mustang.agent/web_fetch/set_backend", {"backend": "auto"})
                    await client._request("_mustang.agent/web_bridge/pair_reset", {})

    _run(_probe())
