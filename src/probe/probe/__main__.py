"""Entry point for deepcli-probe.

Usage (interactive)::

    python -m probe [--port 8200] [--session SESSION_ID] [--password PW]

Usage (machine-readable, for automated verification)::

    python -m probe --test --prompt "hello"
    # stdout: {"ok": true, "stop_reason": "end_turn", "text": "...", "tools": [...]}
    # exit 0 on success, 1 on error

The --test flag is intended for use by AI agents (CI scripts, verification
runs) that need a parseable JSON result rather than a human-readable stream.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .client import (
    AgentChunk,
    KernelNotRunning,
    PermissionRequest,
    ProbeClient,
    ToolCallEvent,
    ToolCallUpdate,
    TurnComplete,
)
from .repl import run_repl


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="probe",
        description="deepcli-probe: ACP test client for DeepCLI's Mustang kernel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8200,
        metavar="PORT",
        help="kernel port (default: 8200)",
    )
    parser.add_argument(
        "--session",
        metavar="SESSION_ID",
        help="load an existing session instead of creating a new one",
    )
    parser.add_argument(
        "--password",
        metavar="PW",
        help="use password auth instead of reading the token file",
    )
    parser.add_argument(
        "--cwd",
        metavar="DIR",
        help="working directory for the session (default: current dir)",
    )
    parser.add_argument(
        "--meta",
        metavar="JSON",
        help='ACP _meta extension as JSON string, e.g. \'{"worktree":{"slug":"feat"}}\'',
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="machine-readable mode: print JSON result and exit",
    )
    parser.add_argument(
        "--prompt",
        metavar="TEXT",
        help="prompt text for --test mode",
    )
    parser.add_argument(
        "--client-turn-id",
        metavar="UUID",
        help="fixed mustang.agent/clientTurnId for --test prompt retry probes",
    )
    parser.add_argument(
        "--raw",
        nargs=2,
        metavar=("METHOD", "PARAMS_JSON"),
        help='send a raw ACP request: --raw _mustang.agent/secrets/auth \'{"action":"list"}\'',
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="print raw WebSocket frames to stderr",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    """Connect, initialise, and dispatch to the correct mode."""
    try:
        async with ProbeClient(port=args.port, password=args.password, debug=args.debug) as client:
            await client.initialize()

            # --raw: fire a single ACP request and print the result.
            if args.raw:
                return await _run_raw(client, args.raw[0], args.raw[1])

            # Parse --meta JSON if provided.
            meta: dict | None = None
            if args.meta:
                try:
                    meta = json.loads(args.meta)
                except json.JSONDecodeError as exc:
                    print(f"[probe] invalid --meta JSON: {exc}", file=sys.stderr)
                    return 1

            cwd = args.cwd or str(Path.cwd())

            if args.session:
                history = await client.load_session(args.session, cwd=cwd)
                session_id: str = args.session
            else:
                session_id = await client.new_session(cwd=cwd, meta=meta)
                history = []

            if args.test:
                return await _run_test(client, session_id, args.prompt, args.client_turn_id)

            # Print replayed history before entering the REPL.
            for event in history:
                print(event)

            await run_repl(client, session_id)
            return 0

    except KernelNotRunning as exc:
        if args.test:
            _emit_json({"ok": False, "error": str(exc)})
        else:
            print(f"[probe] error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if args.test:
            _emit_json({"ok": False, "error": str(exc)})
        else:
            print(f"[probe] unexpected error: {exc}", file=sys.stderr)
        return 1


async def _run_raw(client: ProbeClient, method: str, params_json: str) -> int:
    """Send a single raw ACP request and print the JSON result.

    Usage::

        python -m probe --raw _mustang.agent/secrets/auth '{"action":"list"}'
    """
    try:
        params = json.loads(params_json)
    except json.JSONDecodeError as exc:
        print(f"[probe] invalid JSON params: {exc}", file=sys.stderr)
        return 1

    try:
        result = await client._request(method, params)
        _emit_json(result)
        return 0
    except Exception as exc:
        _emit_json({"ok": False, "error": str(exc)})
        return 1


async def _run_test(
    client: ProbeClient,
    session_id: str,
    prompt_text: str | None,
    client_turn_id: str | None,
) -> int:
    """Send a single prompt and emit a JSON result to stdout.

    Schema::

        {
            "ok": bool,
            "stop_reason": str,   # only on ok=true
            "text": str,          # concatenated agent text chunks
            "tools": [            # tool calls that occurred
                {"id": str, "title": str, "kind": str}
            ],
            "error": str          # only on ok=false
        }
    """
    if not prompt_text:
        _emit_json({"ok": False, "error": "--prompt is required in --test mode"})
        return 1

    text_chunks: list[str] = []
    tools: list[dict[str, str]] = []
    tool_updates: list[dict[str, str]] = []
    permission_requests: list[dict[str, object]] = []
    stop_reason = "unknown"

    try:
        async for event in client.prompt(
            session_id,
            prompt_text,
            client_turn_id=client_turn_id,
        ):
            if isinstance(event, AgentChunk):
                text_chunks.append(event.text)
            elif isinstance(event, ToolCallEvent):
                tools.append(
                    {
                        "id": event.tool_call_id,
                        "title": event.title,
                        "kind": event.kind,
                    }
                )
            elif isinstance(event, ToolCallUpdate):
                tool_updates.append(
                    {
                        "id": event.tool_call_id,
                        "status": event.status,
                    }
                )
            elif isinstance(event, PermissionRequest):
                permission_requests.append(
                    {
                        "toolCallId": event.tool_call_id,
                        "toolTitle": event.tool_title,
                        "options": event.options,
                        "decision": "allow_once",
                    }
                )
                await client.reply_permission(event.req_id, "allow_once")
            elif isinstance(event, TurnComplete):
                stop_reason = event.stop_reason

        _emit_json(
            {
                "ok": True,
                "sessionId": session_id,
                "promptCompleted": True,
                "stop_reason": stop_reason,
                "stopReason": stop_reason,
                "text": "".join(text_chunks),
                "tools": tools,
                "toolCalls": [tool["title"] for tool in tools],
                "toolUpdates": tool_updates,
                "permissionRequests": permission_requests,
                "errors": [],
            }
        )
        return 0

    except Exception as exc:
        _emit_json({"ok": False, "error": str(exc)})
        return 1


def _emit_json(obj: dict) -> None:  # type: ignore[type-arg]
    """Print *obj* as a single JSON line to stdout."""
    print(json.dumps(obj, ensure_ascii=False))


def main() -> None:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(_run(args)))
    except KeyboardInterrupt:
        # Ctrl-C — exit with the conventional SIGINT code, no traceback.
        sys.exit(130)


if __name__ == "__main__":
    main()
