"""E2E probe for WebFetch backend management ACP methods.

This is the CLI-visible closure seam: the same ACP methods power
``/webfetch backend`` and ``/webfetch config``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from probe.client import ProbeClient


def _run(coro: Any) -> Any:
    return asyncio.run(asyncio.wait_for(coro, timeout=30))


def test_webfetch_backend_management_probe(kernel: tuple[int, str]) -> None:
    port, token = kernel

    async def _probe() -> None:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()

            commands = await client._request("_mustang.agent/commands/list", {})
            webfetch = [cmd for cmd in commands.get("commands", []) if cmd.get("name") == "webfetch"]
            assert webfetch, "commands/list must expose /webfetch to the CLI"
            assert webfetch[0].get("subcommands") == ["backend", "browser", "config"]

            options = await client._request("_mustang.agent/web_fetch/backend_options", {})
            ids = [item["id"] for item in options["options"]]
            assert "auto" in ids
            assert "httpx" in ids
            assert "crawl4ai" in ids
            assert "browser" in ids
            assert "readability" not in ids
            assert "playwright" not in ids

            set_result = await client._request(
                "_mustang.agent/web_fetch/set_backend",
                {"backend": "httpx"},
            )
            assert set_result["backend"] == "httpx"
            assert set_result["setupRequired"] is False

            config = await client._request("_mustang.agent/web_fetch/get_config", {})
            assert config["backend"] == "httpx"

            updated = await client._request(
                "_mustang.agent/web_fetch/set_config",
                {"path": "crawl4ai.timeout_seconds", "value": 45},
            )
            assert updated["backends"]["crawl4ai"]["timeout_seconds"] == 45

            await client._request("_mustang.agent/web_fetch/set_backend", {"backend": "auto"})

    _run(_probe())
