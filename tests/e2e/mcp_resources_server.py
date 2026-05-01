#!/usr/bin/env python3
"""MCP server with resources support for probe / e2e testing.

Speaks JSON-RPC over stdin/stdout with Content-Length framing.

Capabilities advertised:
  - tools:     one ``echo`` tool (inherited from echo server)
  - resources: a small set of in-memory resources

Resources exposed:
  notes://daily/today          plain-text daily note
  config://app/settings        JSON app settings
  data://metrics/summary       plain-text metrics summary
  image://logo/png             tiny 1×1 red PNG (binary blob, base64)

Usage::

    python mcp_resources_server.py
"""

from __future__ import annotations

import base64
import json
import sys

# ── tiny 1×1 red PNG (22 bytes uncompressed) ───────────────────────────────
_RED_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
)

_RESOURCES = [
    {
        "uri": "notes://daily/today",
        "name": "Today's daily note",
        "description": "A short daily note written today.",
        "mimeType": "text/plain",
    },
    {
        "uri": "config://app/settings",
        "name": "App settings",
        "description": "Application configuration (JSON).",
        "mimeType": "application/json",
    },
    {
        "uri": "data://metrics/summary",
        "name": "Metrics summary",
        "description": "High-level system metrics for today.",
        "mimeType": "text/plain",
    },
    {
        "uri": "image://logo/png",
        "name": "Logo (PNG)",
        "description": "Tiny test logo image (binary blob).",
        "mimeType": "image/png",
    },
]

_RESOURCE_CONTENTS: dict[str, dict] = {
    "notes://daily/today": {
        "uri": "notes://daily/today",
        "mimeType": "text/plain",
        "text": (
            "# Daily Note — 2026-04-25\n\n"
            "- Implemented ListMcpResources and ReadMcpResource tools\n"
            "- Wrote mcp_resources_server.py for probe testing\n"
            "- All unit tests passing\n"
        ),
    },
    "config://app/settings": {
        "uri": "config://app/settings",
        "mimeType": "application/json",
        "text": json.dumps(
            {
                "app": "mustang-probe",
                "version": "0.1.0",
                "debug": False,
                "features": {"mcp_resources": True, "tool_search": True},
            },
            indent=2,
        ),
    },
    "data://metrics/summary": {
        "uri": "data://metrics/summary",
        "mimeType": "text/plain",
        "text": (
            "System metrics — 2026-04-25\n"
            "  active_sessions : 3\n"
            "  tool_calls_today: 142\n"
            "  mcp_tool_calls  : 27\n"
            "  p99_latency_ms  : 312\n"
        ),
    },
    "image://logo/png": {
        "uri": "image://logo/png",
        "mimeType": "image/png",
        "blob": _RED_PNG_B64,
    },
}


# ── JSON-RPC framing ────────────────────────────────────────────────────────


def _read_message() -> dict | None:
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        text = line.decode(errors="replace").strip()
        if text.lower().startswith("content-length:"):
            length = int(text.split(":", 1)[1].strip())
            sys.stdin.buffer.readline()  # blank line
            body = sys.stdin.buffer.read(length)
            return json.loads(body)


def _write_message(msg: dict) -> None:
    body = json.dumps(msg).encode()
    header = f"Content-Length: {len(body)}\r\n\r\n".encode()
    sys.stdout.buffer.write(header + body)
    sys.stdout.buffer.flush()


# ── Request handler ─────────────────────────────────────────────────────────


def _handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    msg_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                    "resources": {},
                },
                "serverInfo": {"name": "resources-server", "version": "0.1.0"},
                "instructions": (
                    "This server exposes test resources at notes://, config://, "
                    "data://, and image:// URIs.  Use ListMcpResources to "
                    "discover them, then ReadMcpResource to read their contents."
                ),
            },
        }

    if method == "initialized":
        return None

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echoes the input message back as text.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "message": {"type": "string", "description": "The message to echo."}
                            },
                            "required": ["message"],
                        },
                    }
                ]
            },
        }

    if method == "tools/call":
        params = msg.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        if tool_name == "echo":
            text = arguments.get("message", "")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"echo: {text}"}],
                    "isError": False,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": f"unknown tool: {tool_name}"}],
                "isError": True,
            },
        }

    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"resources": _RESOURCES},
        }

    if method == "resources/read":
        params = msg.get("params", {})
        uri = params.get("uri", "")
        content = _RESOURCE_CONTENTS.get(uri)
        if content is None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32602,
                    "message": f"Resource not found: {uri}",
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"contents": [content]},
        }

    # Unknown method.
    if msg_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return None


def main() -> None:
    while True:
        msg = _read_message()
        if msg is None:
            break
        resp = _handle(msg)
        if resp is not None:
            _write_message(resp)


if __name__ == "__main__":
    main()
