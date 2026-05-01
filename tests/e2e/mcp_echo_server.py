#!/usr/bin/env python3
"""Minimal MCP echo server for e2e testing.

Speaks JSON-RPC over stdin/stdout with Content-Length framing.
Advertises one tool ``echo`` that returns its input as text.

Usage::

    python mcp_echo_server.py
"""

from __future__ import annotations

import json
import sys


def _read_message() -> dict | None:
    """Read one Content-Length framed JSON-RPC message from stdin."""
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None  # EOF
        text = line.decode(errors="replace").strip()
        if text.lower().startswith("content-length:"):
            length = int(text.split(":", 1)[1].strip())
            # Consume blank line after header.
            sys.stdin.buffer.readline()
            body = sys.stdin.buffer.read(length)
            return json.loads(body)


def _write_message(msg: dict) -> None:
    """Write one Content-Length framed JSON-RPC message to stdout."""
    body = json.dumps(msg).encode()
    header = f"Content-Length: {len(body)}\r\n\r\n".encode()
    sys.stdout.buffer.write(header + body)
    sys.stdout.buffer.flush()


def _handle(msg: dict) -> dict | None:
    """Handle a JSON-RPC request; return response or None for notifications."""
    method = msg.get("method", "")
    msg_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo-server", "version": "0.1.0"},
            },
        }

    if method == "initialized":
        return None  # notification, no response

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
                                "message": {
                                    "type": "string",
                                    "description": "The message to echo.",
                                }
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
            break  # stdin closed
        resp = _handle(msg)
        if resp is not None:
            _write_message(resp)


if __name__ == "__main__":
    main()
