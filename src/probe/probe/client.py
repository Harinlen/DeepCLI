"""ACP WebSocket client for deepcli-probe.

Wraps the JSON-RPC 2.0 / ACP wire protocol so callers work with typed
Python objects.  All I/O is async; no printing or user interaction here.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator

import websockets
from websockets.asyncio.client import ClientConnection

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_TOKEN_PATH = Path.home() / ".deepcli" / "state" / "auth_token"


# ---------------------------------------------------------------------------
# Event types  (what callers receive from prompt() and load_session())
# ---------------------------------------------------------------------------


@dataclass
class AgentChunk:
    """A streamed text chunk from the agent."""

    text: str


@dataclass
class UserChunk:
    """A replayed user-message chunk (session/load history)."""

    text: str


@dataclass
class ToolCallEvent:
    """The agent announced a new tool call."""

    tool_call_id: str
    title: str
    kind: str  # read | edit | execute | fetch | other …
    status: str  # pending | in_progress | completed | cancelled


@dataclass
class ToolCallUpdate:
    """Status update for an already-announced tool call."""

    tool_call_id: str
    status: str
    meta: dict[str, Any] | None = None


@dataclass
class UserExecutionEvent:
    """Kernel-side user REPL execution event for ``!`` and ``$`` probes."""

    phase: str
    kind: str
    execution_id: str
    stream: str | None = None
    text: str = ""
    exit_code: int | None = None
    cancelled: bool = False


@dataclass
class UsageEvent:
    """Current context snapshot from a ``usage_update`` notification."""

    input_tokens: int
    output_tokens: int
    used: int
    size: int | None = None
    duration_ms: int | None = None


@dataclass
class PermissionRequest:
    """Kernel asks the client whether a tool call is allowed.

    The caller must respond with reply_permission(req_id, option_id).
    """

    req_id: int
    session_id: str
    tool_call_id: str
    options: list[dict[str, str]]  # list of {optionId, name, kind}
    tool_title: str | None = None  # human-readable tool name
    input_summary: str | None = None  # one-line description of what the tool will do
    tool_input: dict[str, Any] | None = None  # raw tool input for tool-specific UIs


@dataclass
class TurnComplete:
    """A session/prompt turn has finished."""

    stop_reason: str
    error: "ProbeError | None" = field(default=None, repr=False)


# Union of everything that can be yielded from prompt() / load_session().
Event = (
    AgentChunk
    | UserChunk
    | ToolCallEvent
    | ToolCallUpdate
    | UserExecutionEvent
    | UsageEvent
    | PermissionRequest
    | TurnComplete
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ProbeError(Exception):
    """Raised when the kernel returns a JSON-RPC error response."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.rpc_message = message


class KernelNotRunning(Exception):
    """Raised when a connection to the kernel cannot be established."""


# ---------------------------------------------------------------------------
# ProbeClient
# ---------------------------------------------------------------------------


class ProbeClient:
    """Async context-manager ACP client.

    Usage::

        async with ProbeClient(port=8200) as client:
            await client.initialize()
            sid = await client.new_session()
            async for event in client.prompt(sid, "hello"):
                if isinstance(event, AgentChunk):
                    print(event.text, end="", flush=True)

    The client reads the auth token from ``~/.deepcli/state/auth_token``
    by default.  Pass *password* to use password-based auth instead.
    """

    # Default timeout (seconds) for simple RPC round-trips (session/new,
    # model/*, etc.).  Generous enough for kernel startup, tight enough to
    # surface hangs.  Does NOT apply to prompt() — turns wait indefinitely
    # because they include user interaction (permission prompts, etc.).
    DEFAULT_REQUEST_TIMEOUT: float = 30.0

    def __init__(
        self,
        port: int = 8200,
        password: str | None = None,
        token: str | None = None,
        debug: bool = False,
        request_timeout: float | None = None,
    ) -> None:
        self._port = port
        self._password = password
        self._token = token  # explicit token overrides file read
        self._debug = debug  # print raw frames to stderr when True
        self._request_timeout = request_timeout or self.DEFAULT_REQUEST_TIMEOUT

        self._ws: ClientConnection | None = None
        self._recv_task: asyncio.Task[None] | None = None

        # Auto-incrementing request IDs.
        self._req_id = 0

        # Futures for standard request/response pairs.
        self._pending: dict[int, asyncio.Future[Any]] = {}

        # Method name per pending request — needed to route session/prompt
        # responses as TurnComplete sentinels instead of futures.
        self._pending_methods: dict[int, str] = {}

        # Single queue for all inbound events (session/update, permission
        # requests, and TurnComplete sentinels).  prompt() drains it.
        self._events: asyncio.Queue[Event] = asyncio.Queue()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "ProbeClient":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _ws_url(self) -> str:
        """Build the WebSocket URL, embedding the auth credential."""
        if self._password is not None:
            cred = f"password={self._password}"
        else:
            token = self._token or _read_token()
            cred = f"token={token}"
        return f"ws://127.0.0.1:{self._port}/session?{cred}"

    async def connect(self) -> None:
        """Open the WebSocket and start the background receive loop."""
        url = self._ws_url()
        try:
            self._ws = await websockets.connect(url)
        except OSError as exc:
            raise KernelNotRunning(
                f"Cannot connect to kernel at port {self._port}. Is `scripts/run-kernel.sh` running?"
            ) from exc
        self._recv_task = asyncio.create_task(self._recv_loop(), name="probe-recv")

    async def close(self) -> None:
        """Cancel the receive loop and close the WebSocket."""
        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._ws is not None:
            await self._ws.close()

    # ------------------------------------------------------------------
    # Low-level send helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _send(self, msg: dict[str, Any]) -> None:
        assert self._ws is not None, "Not connected"
        await self._ws.send(json.dumps(msg))

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> Any:
        """Send a JSON-RPC request and block until its response arrives.

        Raises ``asyncio.TimeoutError`` if no response within *timeout*
        seconds (defaults to ``self._request_timeout``).
        """
        req_id = self._next_id()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[req_id] = fut
        self._pending_methods[req_id] = method
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        t = timeout if timeout is not None else self._request_timeout
        try:
            return await asyncio.wait_for(fut, timeout=t)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            self._pending_methods.pop(req_id, None)
            raise asyncio.TimeoutError(
                f"Kernel did not respond to {method!r} (id={req_id}) within {t}s"
            ) from None

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # ------------------------------------------------------------------
    # Background receive loop
    # ------------------------------------------------------------------

    async def _recv_loop(self) -> None:
        """Route every inbound frame to its appropriate destination.

        Per-frame errors are caught and logged without killing the loop;
        letting a single malformed message terminate recv would silently
        hang every pending future, which is much harder to debug than a
        noisy log line.
        """
        import sys

        assert self._ws is not None
        try:
            async for raw in self._ws:
                if self._debug:
                    text = raw if isinstance(raw, str) else raw.decode()
                    print(f"[debug] << {text}", file=sys.stderr, flush=True)
                try:
                    msg: dict[str, Any] = json.loads(raw)
                    if "id" in msg and ("result" in msg or "error" in msg):
                        self._route_response(msg)
                    elif msg.get("method") == "session/update":
                        event = _parse_update(msg)
                        if event is not None:
                            await self._events.put(event)
                    elif msg.get("method") == "_mustang.agent/session/execution_update":
                        await self._events.put(_parse_execution_update(msg))
                    elif msg.get("method") == "session/request_permission":
                        await self._events.put(_parse_permission(msg))
                except Exception as exc:
                    # Surface the raw frame + error so a schema drift is
                    # immediately obvious instead of manifesting as a hang.
                    print(
                        f"[probe] warn: failed to parse frame ({exc}): {str(raw)[:300]}",
                        file=sys.stderr,
                        flush=True,
                    )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            # Connection-level failure (WebSocket closed, etc.) — log but
            # still exit silently after: caller observes disconnect via
            # pending futures timing out or subsequent sends failing.
            print(f"[probe] recv_loop exited: {exc}", file=sys.stderr, flush=True)

    def _route_response(self, msg: dict[str, Any]) -> None:
        """Resolve the pending future for this response.

        All methods, including ``session/prompt``, now resolve a future.
        ``prompt()`` uses ``asyncio.wait`` so it can detect the response
        arriving before or after the streaming ``session/update`` chunks
        (the kernel sends the response first in practice).
        """
        req_id: int = msg["id"]
        self._pending_methods.pop(req_id, None)

        fut = self._pending.pop(req_id, None)
        if fut is None or fut.done():
            return
        if "error" in msg:
            err = msg["error"]
            fut.set_exception(ProbeError(err["code"], err["message"]))
        else:
            fut.set_result(msg.get("result"))

    # ------------------------------------------------------------------
    # ACP methods
    # ------------------------------------------------------------------

    async def initialize(self) -> dict[str, Any]:
        """Send ACP ``initialize`` and return observable capability fields.

        Older kernel paths exposed only ``agentCapabilities`` to callers.  The
        Access Router runtime path returns ACP's full initialize result with
        additional top-level capability groups, so keep the legacy fields while
        preserving those top-level groups for E2E probes.
        """
        result: dict[str, Any] = await self._request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "probe", "version": "0.1.0"},
            },
        )
        caps: dict[str, Any] = dict(result.get("agentCapabilities", {}))
        for key in (
            "promptCapabilities",
            "mcpCapabilities",
            "sessionCapabilities",
            "_meta",
        ):
            if key in result:
                caps[key] = result[key]
        return caps

    async def new_session(
        self,
        cwd: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        """Create a new session and return its ``sessionId``.

        Args:
            cwd: Working directory for the session.
            meta: ACP ``_meta`` extension dict.  Used for worktree
                startup mode (``{"worktree": {"slug": "..."}}``) and
                future protocol extensions.
        """
        params: dict[str, Any] = {
            "cwd": cwd or str(Path.cwd()),
            "mcpServers": [],
        }
        if meta is not None:
            params["meta"] = meta
        result: dict[str, Any] = await self._request("session/new", params)
        session_id: str = result["sessionId"]
        return session_id

    async def load_session(
        self,
        session_id: str,
        cwd: str | None = None,
    ) -> list[Event]:
        """Load an existing session and return its replayed history events.

        Per the ACP spec, the kernel streams all history as ``session/update``
        notifications *before* sending the ``session/load`` response.  By the
        time ``_request`` returns, every history event is already in the event
        queue — we drain it here so prompt() sees a clean queue.
        """
        await self._request(
            "session/load",
            {
                "sessionId": session_id,
                "cwd": cwd or str(Path.cwd()),
                "mcpServers": [],
            },
        )
        history: list[Event] = []
        while not self._events.empty():
            history.append(self._events.get_nowait())
        return history

    async def resume_session(
        self,
        session_id: str,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Resume an existing session without replaying history."""
        result: dict[str, Any] = await self._request(
            "session/resume",
            {
                "sessionId": session_id,
                "cwd": cwd or str(Path.cwd()),
            },
        )
        return result

    async def close_session(self, session_id: str) -> dict[str, Any]:
        """Close active runtime resources for a session while keeping history."""
        result: dict[str, Any] = await self._request(
            "session/close",
            {"sessionId": session_id},
        )
        return result

    async def list_sessions(
        self, *, cursor: str | None = None, cwd: str | None = None
    ) -> dict[str, Any]:
        """List persisted sessions through ACP."""
        result: dict[str, Any] = await self._request(
            "session/list",
            {
                "cursor": cursor,
                "cwd": cwd,
                "meta": {"mustang.agent/includeEmpty": True},
            },
        )
        return result

    async def prompt(
        self,
        session_id: str,
        text: str,
        *,
        client_turn_id: str | None = None,
        timeout: float | None = None,
    ) -> AsyncGenerator[Event, None]:
        """Send a prompt and yield events until the turn completes.

        Yields ``AgentChunk``, ``ToolCallEvent``, ``ToolCallUpdate``, and
        ``PermissionRequest`` as they arrive.  The final event is always
        ``TurnComplete``; if the kernel returned an error, ``ProbeError`` is
        raised instead.

        *timeout* caps the entire turn.  Defaults to ``None`` (no limit)
        because a turn includes user interaction (permission prompts, plan
        approval, etc.) whose duration is unbounded.  Each inner layer —
        LLM provider, tool execution, MCP transport — carries its own
        timeout, so a blanket deadline here would only mis-fire on
        legitimate user think-time.

        Raises ``asyncio.TimeoutError`` if *timeout* is set and exceeded.

        **Why two phases?**  The kernel sends the ``session/prompt`` response
        (``stopReason``) *before* the streaming ``session/update`` chunks,
        which is the reverse of the ACP spec order.  A sentinel-in-queue
        approach would stop the generator before any text was yielded.
        Instead we use a future for the response and drain the event queue
        with a short timeout after the future resolves.
        """
        t = timeout  # None → wait forever
        deadline = (asyncio.get_event_loop().time() + t) if t is not None else None

        req_id = self._next_id()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[req_id] = fut
        self._pending_methods[req_id] = "session/prompt"

        params: dict[str, Any] = {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}],
        }
        if client_turn_id:
            params["_meta"] = {"mustang.agent/clientTurnId": client_turn_id}

        await self._send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "session/prompt",
                "params": params,
            }
        )

        def _remaining() -> float | None:
            if deadline is None:
                return None  # no timeout
            rem = deadline - asyncio.get_event_loop().time()
            if rem <= 0:
                raise asyncio.TimeoutError(f"prompt() timed out after {t}s (session={session_id})")
            return rem

        # Phase 1 — race: yield events as they arrive; stop when the response
        # future resolves (whichever comes first on the wire).
        while not fut.done():
            get_task: asyncio.Task[Event] = asyncio.ensure_future(self._events.get())
            done, _ = await asyncio.wait(
                {get_task, fut},
                return_when=asyncio.FIRST_COMPLETED,
                timeout=_remaining(),
            )
            if not done:
                # wait() returned empty because of timeout — nothing finished.
                get_task.cancel()
                raise asyncio.TimeoutError(f"prompt() timed out after {t}s (session={session_id})")
            if get_task not in done:
                # fut won the race; cancel the pending queue read.
                get_task.cancel()
                try:
                    await get_task
                except (asyncio.CancelledError, Exception):
                    pass
            else:
                yield get_task.result()

        # Phase 2 — drain: the response is in; yield any chunks that arrived
        # after it (50 ms window covers network batching on a local kernel).
        while True:
            try:
                event = await asyncio.wait_for(self._events.get(), timeout=0.05)
                yield event
            except asyncio.TimeoutError:
                break

        # Propagate errors or emit the terminal event.
        exc = fut.exception()
        if exc is not None:
            raise exc
        result: dict[str, Any] = fut.result() or {}
        yield TurnComplete(stop_reason=result.get("stopReason", "unknown"))

    async def set_mode(self, session_id: str, mode_id: str) -> None:
        """Send ``session/set_mode`` to switch permission mode."""
        await self._request(
            "session/set_mode",
            {"sessionId": session_id, "modeId": mode_id},
        )

    async def cancel(self, session_id: str) -> None:
        """Send ``session/cancel`` to interrupt the current prompt turn."""
        await self._notify("session/cancel", {"sessionId": session_id})

    async def execute_shell(
        self,
        session_id: str,
        command: str,
        *,
        exclude_from_context: bool = True,
        shell: str = "auto",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Execute shell through the Mustang ACP extension namespace."""
        return await self._request(
            "_mustang.agent/session/execute_shell",
            {
                "sessionId": session_id,
                "command": command,
                "excludeFromContext": exclude_from_context,
                "shell": shell,
            },
            timeout=timeout,
        )

    async def execute_python(
        self,
        session_id: str,
        code: str,
        *,
        exclude_from_context: bool = True,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Execute Python through the Mustang ACP extension namespace."""
        return await self._request(
            "_mustang.agent/session/execute_python",
            {
                "sessionId": session_id,
                "code": code,
                "excludeFromContext": exclude_from_context,
            },
            timeout=timeout,
        )

    async def cancel_execution(self, session_id: str, kind: str = "any") -> None:
        """Cancel user REPL execution through the Mustang extension namespace."""
        await self._notify(
            "_mustang.agent/session/cancel_execution",
            {"sessionId": session_id, "kind": kind},
        )

    async def drain_events(self, *, timeout: float = 0.05) -> list[Event]:
        """Drain queued probe events after a non-prompt request completes."""
        events: list[Event] = []
        while True:
            try:
                events.append(await asyncio.wait_for(self._events.get(), timeout=timeout))
            except asyncio.TimeoutError:
                return events

    # ------------------------------------------------------------------
    # Provider / model management
    # ------------------------------------------------------------------

    async def list_providers(self) -> dict[str, Any]:
        """Send Mustang ``model/provider_list`` and return the result dict."""
        return await self._request("_mustang.agent/model/provider_list", {})

    async def add_provider(
        self,
        name: str,
        provider_type: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        aws_secret_key: str | None = None,
        aws_region: str | None = None,
        models: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send Mustang ``model/provider_add`` and return the result dict."""
        params: dict[str, Any] = {
            "name": name,
            "providerType": provider_type,
        }
        if api_key is not None:
            params["apiKey"] = api_key
        if base_url is not None:
            params["baseUrl"] = base_url
        if aws_secret_key is not None:
            params["awsSecretKey"] = aws_secret_key
        if aws_region is not None:
            params["awsRegion"] = aws_region
        if models is not None:
            params["models"] = models
        return await self._request("_mustang.agent/model/provider_add", params)

    async def remove_provider(self, name: str) -> dict[str, Any]:
        """Send Mustang ``model/provider_remove`` and return the result dict."""
        return await self._request("_mustang.agent/model/provider_remove", {"name": name})

    async def refresh_models(self, name: str) -> dict[str, Any]:
        """Send Mustang ``model/provider_refresh`` and return the result dict."""
        return await self._request("_mustang.agent/model/provider_refresh", {"name": name})

    async def set_current_model(
        self,
        provider: str,
        model: str,
        *,
        role: str = "default",
    ) -> dict[str, Any]:
        """Send Mustang ``model/set_current`` and return the result dict."""
        return await self._request(
            "_mustang.agent/model/set_current",
            {"role": role, "provider": provider, "model": model},
        )

    # ------------------------------------------------------------------
    # Permission reply
    # ------------------------------------------------------------------

    async def reply_permission(
        self,
        req_id: int,
        option_id: str,
        *,
        updated_input: dict[str, Any] | None = None,
    ) -> None:
        """Respond to a ``session/request_permission`` request.

        Args:
            req_id: The JSON-RPC request ID from the ``PermissionRequest``.
            option_id: The ``optionId`` chosen by the user.
            updated_input: Optional rewritten tool input (e.g. user answers
                for ``AskUserQuestionTool``).  When present, forwarded to
                ``PermissionResponse.updated_input``.
        """
        outcome: dict[str, Any] = {"outcome": "selected", "optionId": option_id}
        if updated_input is not None:
            outcome["updatedInput"] = updated_input
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"outcome": outcome},
            }
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _read_token() -> str:
    """Read the auth token from the standard mustang state directory."""
    if not _TOKEN_PATH.exists():
        raise FileNotFoundError(
            f"Auth token not found at {_TOKEN_PATH}. "
            "Is the kernel running?  Use --password for password-based auth."
        )
    return _TOKEN_PATH.read_text().strip()


def _parse_update(msg: dict[str, Any]) -> Event | None:
    """Convert a ``session/update`` notification to a typed event.

    Returns ``None`` for update types we don't handle (plan, mode_change …).
    """
    update: dict[str, Any] = msg["params"]["update"]
    kind: str = update.get("sessionUpdate", "")

    if kind == "agent_message_chunk":
        content = update.get("content") or {}
        return AgentChunk(text=content.get("text", ""))

    if kind == "user_message_chunk":
        content = update.get("content") or {}
        return UserChunk(text=content.get("text", ""))

    if kind == "tool_call":
        return ToolCallEvent(
            tool_call_id=update["toolCallId"],
            title=update.get("title", ""),
            kind=update.get("kind", "other"),
            status=update.get("status", "pending"),
        )

    if kind == "tool_call_update":
        return ToolCallUpdate(
            tool_call_id=update["toolCallId"],
            status=update.get("status", ""),
            meta=update.get("_meta") or update.get("meta"),
        )

    if kind == "usage_update":
        return UsageEvent(
            input_tokens=int(update.get("inputTokens") or update.get("input_tokens") or 0),
            output_tokens=int(update.get("outputTokens") or update.get("output_tokens") or 0),
            used=int(update.get("used") or 0),
            size=update.get("size"),
            duration_ms=update.get("durationMs") or update.get("duration_ms"),
        )

    return None  # unknown / unhandled update type


def _parse_execution_update(msg: dict[str, Any]) -> UserExecutionEvent:
    params: dict[str, Any] = msg["params"]
    execution: dict[str, Any] = params["execution"]
    return UserExecutionEvent(
        phase=execution.get("phase", ""),
        kind=execution.get("kind", ""),
        execution_id=execution.get("executionId", ""),
        stream=execution.get("stream"),
        text=execution.get("text", "") or "",
        exit_code=execution.get("exitCode"),
        cancelled=bool(execution.get("cancelled", False)),
    )


def _parse_permission(msg: dict[str, Any]) -> PermissionRequest:
    """Convert a ``session/request_permission`` request to a typed event."""
    params: dict[str, Any] = msg["params"]
    tool_call = params["toolCall"]
    return PermissionRequest(
        req_id=int(msg["id"]),
        session_id=params["sessionId"],
        tool_call_id=tool_call["toolCallId"],
        options=params.get("options", []),
        tool_title=tool_call.get("title"),
        input_summary=tool_call.get("inputSummary") or tool_call.get("input_summary"),
        tool_input=params.get("toolInput"),
    )
