"""External ACP stdio runtime adapter.

The adapter speaks structured JSON-RPC over Content-Length framed stdio.  It
does not shell out to ``acpx`` and does not scrape PTY output.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from kernel.agents.mustang.mcp.transport.stdio import StdioTransport


@dataclass(frozen=True)
class ExternalAcpPromptResult:
    """Result of one external ACP prompt call."""

    stop_reason: str
    updates: tuple[dict[str, Any], ...] = ()


@dataclass
class ExternalAcpRuntimeAdapter:
    """Minimal ACP stdio client for third-party runtimes."""

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    request_timeout: float = 10.0

    def __post_init__(self) -> None:
        self._transport = StdioTransport(self.command, self.args, self.env)
        self._next_id = 1
        self._updates: list[dict[str, Any]] = []

    async def connect(self) -> None:
        """Spawn the external runtime process."""

        await self._transport.connect()

    async def close(self) -> None:
        """Close the external runtime process."""

        await self._transport.close()

    async def initialize(self) -> dict[str, Any]:
        """Send ACP initialize."""

        return await self.request("initialize", {"protocolVersion": 1})

    async def new_session(self, *, cwd: str) -> str:
        """Create an ACP session and return its session id."""

        result = await self.request("session/new", {"cwd": cwd, "mcpServers": []})
        return str(result["sessionId"])

    async def prompt(self, *, session_id: str, text: str) -> ExternalAcpPromptResult:
        """Send one ACP prompt and collect structured update notifications."""

        before = len(self._updates)
        result = await self.request(
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": text}],
            },
        )
        return ExternalAcpPromptResult(
            stop_reason=str(result.get("stopReason", "end_turn")),
            updates=tuple(self._updates[before:]),
        )

    async def cancel(self, *, session_id: str) -> None:
        """Send ACP session/cancel notification."""

        await self.notify("session/cancel", {"sessionId": session_id})

    async def close_session(self, *, session_id: str) -> dict[str, Any]:
        """Send ACP session/close."""

        return await self.request("session/close", {"sessionId": session_id})

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one JSON-RPC request and wait for its matching response."""

        request_id = self._allocate_id()
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return await asyncio.wait_for(self._wait_response(request_id), timeout=self.request_timeout)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        """Send one JSON-RPC notification."""

        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def updates(self) -> tuple[dict[str, Any], ...]:
        """Return all structured notifications observed so far."""

        return tuple(self._updates)

    def _allocate_id(self) -> int:
        request_id = self._next_id
        self._next_id += 1
        return request_id

    async def _send(self, frame: dict[str, Any]) -> None:
        await self._transport.send(json.dumps(frame, separators=(",", ":")).encode("utf-8"))

    async def _wait_response(self, request_id: int) -> dict[str, Any]:
        while True:
            frame = json.loads((await self._transport.receive()).decode("utf-8"))
            if "method" in frame and "id" in frame:
                await self._reject_client_request(frame)
                continue
            if "method" in frame:
                self._updates.append(frame)
                continue
            if frame.get("id") != request_id:
                continue
            if error := frame.get("error"):
                raise RuntimeError(str(error.get("message", error)))
            result = frame.get("result") or {}
            if not isinstance(result, dict):
                raise RuntimeError("ACP response result must be an object")
            return result

    async def _reject_client_request(self, frame: dict[str, Any]) -> None:
        """Fail closed for runtime-initiated client authority requests."""

        method = str(frame.get("method", ""))
        request_id = frame.get("id")
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"client call not authorized: {method}",
                },
            }
        )


__all__ = ["ExternalAcpPromptResult", "ExternalAcpRuntimeAdapter"]
