"""Fake external ACP runtime used by adapter tests."""

from __future__ import annotations

import json
import sys


def _read_frame() -> dict:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            raise EOFError
        if line in {b"\r\n", b"\n"}:
            break
        key, value = line.decode().split(":", 1)
        headers[key.lower()] = value.strip()
    body = sys.stdin.buffer.read(int(headers["content-length"]))
    return json.loads(body.decode("utf-8"))


def _write_frame(frame: dict) -> None:
    body = json.dumps(frame, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def _result(request: dict, result: dict) -> None:
    _write_frame({"jsonrpc": "2.0", "id": request["id"], "result": result})


def main() -> None:
    while True:
        try:
            request = _read_frame()
        except EOFError:
            return
        method = request.get("method")
        if "id" not in request:
            continue
        if method == "initialize":
            _result(request, {"protocolVersion": 1, "serverInfo": {"name": "fake-acp"}})
        elif method == "session/new":
            _result(request, {"sessionId": "fake-session"})
        elif method == "session/prompt":
            _write_frame(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "fake-session",
                        "update": {"type": "agent_message_chunk", "text": "pong"},
                    },
                }
            )
            _write_frame(
                {
                    "jsonrpc": "2.0",
                    "id": "client-call-1",
                    "method": "fs/read_text_file",
                    "params": {"path": "/tmp/secret"},
                }
            )
            client_call_response = _read_frame()
            _write_frame(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "fake-session",
                        "update": {
                            "type": "client_call_rejected",
                            "code": client_call_response["error"]["code"],
                        },
                    },
                }
            )
            _result(request, {"stopReason": "end_turn"})
        elif method == "session/close":
            _result(request, {})
        else:
            _write_frame(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {"code": -32601, "message": f"unknown method: {method}"},
                }
            )


if __name__ == "__main__":
    main()
