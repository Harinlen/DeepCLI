"""End-to-end tests for mustang-kernel using ProbeClient.

Each test exercises one or more kernel subsystems through the real ACP
WebSocket interface.  A live kernel must be running (started by the
``kernel`` session fixture in ``conftest.py``).

Coverage map
------------
test_health_endpoint           → Transport (HTTP GET /), FastAPI routes
test_initialize_handshake      → Protocol/ACP (initialize), AcpHandshake
test_new_session_returns_id    → SessionManager.new, SessionStore (SQLite)
test_session_list              → SessionManager.list, SQLite query
test_session_load_replays      → SessionManager.load_session, history replay
test_auth_bad_token_rejected   → ConnectionAuthenticator, transport auth guard
test_before_initialize_error   → AcpSessionHandler pre-init guard
test_model_profile_list        → LLMManager.list_profiles, ModelHandler
test_prompt_basic              → Orchestrator, LLMProvider, full turn pipeline
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any

import pytest
import websockets
import websockets.exceptions

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ProbeClient,
    ProbeError,
    TurnComplete,
    UserExecutionEvent,
    UserChunk,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Timeout for non-LLM operations (handshake, session CRUD, etc.).
_TEST_TIMEOUT: float = 30.0
# Timeout for tests that include LLM round-trips (may need multiple turns).
_LLM_TIMEOUT: float = 90.0


def _run(coro: Any, *, timeout: float = _TEST_TIMEOUT) -> Any:
    """Run an async coroutine with a hard timeout to prevent hangs."""

    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    return asyncio.run(_guarded())


def _client(
    port: int,
    token: str,
    *,
    request_timeout: float = _TEST_TIMEOUT,
    debug: bool = False,
) -> ProbeClient:
    """Create a ProbeClient with the e2e request timeout."""
    return ProbeClient(port=port, token=token, request_timeout=request_timeout, debug=debug)


async def _connected_client(port: int, token: str) -> tuple[ProbeClient, str]:
    """Return (initialized_client, session_id) — caller must close client."""
    client = _client(port, token)
    await client.connect()
    await client.initialize()
    session_id = await client.new_session()
    return client, session_id


# ---------------------------------------------------------------------------
# 1. Health endpoint — Transport + FastAPI routes
# ---------------------------------------------------------------------------


def test_health_endpoint(kernel: tuple[int, str]) -> None:
    """GET / returns 200 with the expected JSON fields.

    Verifies that the FastAPI application is up and the health route is
    registered and reachable before any WebSocket tests run.
    """
    port, _ = kernel
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
        assert resp.status == 200
        body: dict[str, Any] = json.loads(resp.read())

    assert body["name"] == "mustang-kernel"
    assert "version" in body
    assert "boot_time" in body
    assert isinstance(body["boot_time"], float)


# ---------------------------------------------------------------------------
# 2. ACP initialize — Protocol layer + AcpHandshake
# ---------------------------------------------------------------------------


def test_initialize_handshake(kernel: tuple[int, str]) -> None:
    """``initialize`` returns an agent capabilities dict with expected keys.

    Verifies the full ACP handshake path: WS connect → token auth →
    initialize request → InitializeResponse deserialization.
    """
    port, token = kernel

    async def _run_test() -> dict[str, Any]:
        async with _client(port, token) as client:
            caps = await client.initialize()
        return caps

    caps = _run(_run_test())

    # AcpAgentCapabilities fields present in the response.
    assert caps.get("loadSession") is True
    assert "promptCapabilities" in caps
    assert "mcpCapabilities" in caps
    assert "sessionCapabilities" in caps
    meta = caps.get("_meta") or caps.get("meta") or {}
    assert "_mustang.agent/session/execute_shell" in meta["mustang.agent/extensions"]


def test_mustang_extension_user_repl_probe(kernel: tuple[int, str]) -> None:
    """Probe ``!``/``$`` execution through the Mustang extension namespace."""
    port, token = kernel

    async def _run_test() -> tuple[dict[str, Any], dict[str, Any], list[UserExecutionEvent]]:
        async with _client(port, token) as client:
            await client.initialize()
            session_id = await client.new_session()
            shell_result = await client.execute_shell(
                session_id,
                "printf MUSTANG_EXT_SHELL",
                exclude_from_context=True,
            )
            shell_events = await client.drain_events()
            py_result = await client.execute_python(
                session_id,
                "print('MUSTANG_EXT_PY')",
                exclude_from_context=True,
            )
            py_events = await client.drain_events()
        events = [e for e in [*shell_events, *py_events] if isinstance(e, UserExecutionEvent)]
        return shell_result, py_result, events

    shell_result, py_result, events = _run(_run_test())

    assert shell_result["exitCode"] == 0
    assert py_result["exitCode"] == 0
    assert any(e.phase == "start" and e.kind == "shell" for e in events)
    assert any(e.phase == "chunk" and "MUSTANG_EXT_SHELL" in e.text for e in events)
    assert any(e.phase == "end" and e.kind == "shell" and e.exit_code == 0 for e in events)
    assert any(e.phase == "start" and e.kind == "python" for e in events)
    assert any(e.phase == "chunk" and "MUSTANG_EXT_PY" in e.text for e in events)
    assert any(e.phase == "end" and e.kind == "python" and e.exit_code == 0 for e in events)


# ---------------------------------------------------------------------------
# 3. Session/new — SessionManager + SessionStore (SQLite write)
# ---------------------------------------------------------------------------


def test_new_session_returns_id(kernel: tuple[int, str]) -> None:
    """``session/new`` returns a non-empty session ID.

    Verifies: SessionManager.new → SessionStore.create_session → SQLite write.
    """
    port, token = kernel

    async def _run_test() -> str:
        async with _client(port, token) as client:
            await client.initialize()
            return await client.new_session()

    session_id = _run(_run_test())

    assert isinstance(session_id, str)
    assert len(session_id) > 0


# ---------------------------------------------------------------------------
# 4. Session/list — SessionManager.list + SQLite read
# ---------------------------------------------------------------------------


def test_session_list(kernel: tuple[int, str]) -> None:
    """``session/list`` returns a well-formed list of sessions.

    Creates a session first so the list is guaranteed non-empty, then
    calls the raw JSON-RPC ``session/list`` request and validates the
    response shape (list + optional next_cursor key).
    """
    port, token = kernel

    async def _run_test() -> dict[str, Any]:
        async with _client(port, token) as client:
            await client.initialize()
            # Create a session so we know at least one exists.
            await client.new_session()
            # Call session/list directly via _request (not yet a public ProbeClient method).
            result: dict[str, Any] = await client._request(
                "session/list",
                {"cursor": None, "cwd": None},
            )
        return result

    result = _run(_run_test())

    assert "sessions" in result
    assert isinstance(result["sessions"], list)
    # Each session entry must carry at minimum a session_id field.
    for entry in result["sessions"]:
        assert "sessionId" in entry


# ---------------------------------------------------------------------------
# 5. Session/load — history replay (SessionManager + SessionStore read)
# ---------------------------------------------------------------------------


def test_session_load_replays_history(kernel: tuple[int, str]) -> None:
    """Creating a session, sending a prompt, then loading it replays history.

    End-to-end path: SessionManager.new → SessionStore.append_event(user turn)
    → disconnect → SessionManager.load_session → SessionStore.list_events
    → history events streamed as session/update notifications.

    The test asserts that a UserChunk is replayed regardless of whether the
    LLM call succeeded — the user message is persisted before the LLM call.
    """
    port, token = kernel

    async def _create_session_and_prompt() -> str:
        async with _client(port, token, request_timeout=_LLM_TIMEOUT) as client:
            await client.initialize()
            sid = await client.new_session()
            # Send a prompt; we don't care about the LLM response here.
            # We just need a user turn to be persisted.
            try:
                async for _ in client.prompt(sid, "ping"):
                    pass  # drain events without asserting on LLM output
            except Exception:
                pass  # LLM may fail if unconfigured — that's fine
            return sid

    async def _load_and_collect_history(sid: str) -> list[Any]:
        async with _client(port, token) as client:
            await client.initialize()
            history = await client.load_session(sid)
        return history

    session_id = _run(_create_session_and_prompt(), timeout=_LLM_TIMEOUT)
    history = _run(_load_and_collect_history(session_id))

    # At minimum, the user message chunk should be replayed.
    user_chunks = [e for e in history if isinstance(e, UserChunk)]
    assert len(user_chunks) >= 1, f"Expected at least one UserChunk in history, got: {history}"
    # The replayed user text should match what we sent.
    assert "ping" in "".join(c.text for c in user_chunks)


# ---------------------------------------------------------------------------
# 6. Auth rejection — ConnectionAuthenticator + transport auth guard
# ---------------------------------------------------------------------------


def test_auth_bad_token_rejected(kernel: tuple[int, str]) -> None:
    """A wrong token causes the kernel to close the connection with code 4003.

    Verifies that the transport auth guard rejects bad credentials before
    any protocol-layer code runs.  RFC 6455 close code 4003 is the
    kernel's designated "authentication failed" code.
    """
    port, _ = kernel
    url = f"ws://127.0.0.1:{port}/session?token=definitely-wrong-token-xyz"

    async def _run_test() -> int | str:
        try:
            async with websockets.connect(url) as ws:
                # The kernel accepts then closes with 4003 — recv() should raise.
                await ws.recv()
            return "no-error"
        except websockets.exceptions.ConnectionClosedError as exc:
            # rcvd.code is the RFC 6455 close code sent by the server.
            return exc.rcvd.code if exc.rcvd else "no-code"
        except websockets.exceptions.ConnectionClosedOK as exc:
            return exc.rcvd.code if exc.rcvd else "ok-no-code"
        except Exception as exc:
            return f"other:{type(exc).__name__}"

    result = _run(_run_test())
    assert result == 4003, f"Expected close code 4003 (auth failed) from kernel, got: {result!r}"


# ---------------------------------------------------------------------------
# 7. Pre-initialize guard — AcpSessionHandler
# ---------------------------------------------------------------------------


def test_before_initialize_error(kernel: tuple[int, str]) -> None:
    """Sending ``session/new`` before ``initialize`` returns JSON-RPC error -32600.

    Verifies the AcpSessionHandler guard that requires initialize to be
    the first request on a new connection.
    """
    port, token = kernel

    async def _run_test() -> ProbeError:
        async with _client(port, token) as client:
            # Skip initialize() on purpose — raw _request goes straight to wire.
            try:
                await client._request("session/new", {"cwd": "/tmp", "mcpServers": []})
                raise AssertionError("Expected ProbeError, got success")
            except ProbeError as exc:
                return exc

    err = _run(_run_test())

    # JSON-RPC Invalid Request code.
    assert err.code == -32600
    assert "initialize" in err.rpc_message.lower()


# ---------------------------------------------------------------------------
# 8. Model profile list — LLMManager + ModelHandler
# ---------------------------------------------------------------------------


def test_model_profile_list(kernel: tuple[int, str]) -> None:
    """Mustang model profile extension returns profiles and default_model.

    Verifies: ACP routing → LLMManager.list_profiles → response serialization.
    The list may be empty if no models are configured in kernel.yaml — the
    schema itself is what we validate here.
    """
    port, token = kernel

    async def _run_test() -> dict[str, Any]:
        async with _client(port, token) as client:
            await client.initialize()
            result: dict[str, Any] = await client._request(
                "_mustang.agent/model/profile_list",
                {},
            )
        return result

    result = _run(_run_test())

    assert "profiles" in result, f"Missing 'profiles' in response: {result}"
    assert "defaultModel" in result, f"Missing 'defaultModel' in response: {result}"
    assert isinstance(result["profiles"], list)
    # Each profile entry must have the expected fields.
    for profile in result["profiles"]:
        assert "name" in profile
        assert "providerType" in profile
        assert "modelId" in profile
        assert "isDefault" in profile


# ---------------------------------------------------------------------------
# 9. Prompt (full turn) — Orchestrator + LLMProvider + SessionStore
# ---------------------------------------------------------------------------


def test_prompt_basic(kernel: tuple[int, str]) -> None:
    """``session/prompt`` completes a full turn: user → LLM → agent response.

    Verifies the entire orchestration pipeline: prompt queued → Orchestrator
    runs → LLMProvider.stream called → response streamed back → TurnComplete
    with stop_reason == "end_turn".

    Skip condition: if the Mustang model profile extension returns zero
    profiles, the kernel
    has no LLM configured and the test is automatically skipped so it does
    not block CI environments without API keys.
    """
    port, token = kernel

    # Check whether a model is configured before trying a prompt.
    async def _list_profiles() -> list[dict[str, Any]]:
        async with _client(port, token) as client:
            await client.initialize()
            result = await client._request("_mustang.agent/model/profile_list", {})
        return result.get("profiles", [])

    profiles = _run(_list_profiles())
    if not profiles:
        pytest.skip("No LLM model profiles configured in kernel.yaml — skipping prompt test")

    async def _run_prompt() -> tuple[str, str]:
        text_parts: list[str] = []
        stop_reason = "unknown"
        async with _client(port, token, request_timeout=_LLM_TIMEOUT) as client:
            await client.initialize()
            sid = await client.new_session()
            async for event in client.prompt(sid, "Reply with exactly the word: pong"):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, PermissionRequest):
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return "".join(text_parts), stop_reason

    text, stop_reason = _run(_run_prompt(), timeout=_LLM_TIMEOUT)

    assert stop_reason == "end_turn", (
        f"Expected stop_reason='end_turn', got {stop_reason!r}. Agent text: {text!r}"
    )
    assert len(text) > 0, "Expected non-empty agent response"


# ---------------------------------------------------------------------------
# 10. Multi-turn conversation — Orchestrator history accumulation
# ---------------------------------------------------------------------------


def test_multi_turn_conversation(kernel: tuple[int, str]) -> None:
    """Two consecutive prompts in the same session complete successfully.

    Verifies that the Orchestrator correctly accumulates turn history
    and can handle a second prompt after the first completes, testing
    the ``StandardOrchestrator``'s message history management.

    Skipped when no LLM model is configured (same condition as test_prompt_basic).
    """
    port, token = kernel

    async def _list_profiles() -> list[dict[str, Any]]:
        async with _client(port, token) as client:
            await client.initialize()
            result = await client._request("_mustang.agent/model/profile_list", {})
        return result.get("profiles", [])

    profiles = _run(_list_profiles())
    if not profiles:
        pytest.skip("No LLM model profiles configured — skipping multi-turn test")

    async def _run_multi_turn() -> tuple[str, str, str, str]:
        async with _client(port, token, request_timeout=_LLM_TIMEOUT) as client:
            await client.initialize()
            sid = await client.new_session()

            # First turn.
            chunks1: list[str] = []
            stop1 = "unknown"
            async for event in client.prompt(sid, "Say 'one'"):
                if isinstance(event, AgentChunk):
                    chunks1.append(event.text)
                elif isinstance(event, PermissionRequest):
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop1 = event.stop_reason

            # Second turn — should use accumulated history.
            chunks2: list[str] = []
            stop2 = "unknown"
            async for event in client.prompt(sid, "Now say 'two'"):
                if isinstance(event, AgentChunk):
                    chunks2.append(event.text)
                elif isinstance(event, PermissionRequest):
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop2 = event.stop_reason

        return "".join(chunks1), stop1, "".join(chunks2), stop2

    text1, stop1, text2, stop2 = _run(_run_multi_turn(), timeout=_LLM_TIMEOUT)

    assert stop1 == "end_turn", f"Turn 1 stop_reason: {stop1!r}, text: {text1!r}"
    assert stop2 == "end_turn", f"Turn 2 stop_reason: {stop2!r}, text: {text2!r}"
    assert len(text1) > 0, "Turn 1: expected non-empty response"
    assert len(text2) > 0, "Turn 2: expected non-empty response"


# ---------------------------------------------------------------------------
# 11. Session/set_mode — Permission mode switching via ACP
# ---------------------------------------------------------------------------


def test_set_mode_accept_edits(kernel: tuple[int, str]) -> None:
    """``session/set_mode`` switches to ``accept_edits`` without error.

    Verifies the full ACP path: ProbeClient.set_mode → session/set_mode
    request → SessionManager.set_mode → Orchestrator.set_mode.
    No LLM required — this is a pure protocol-layer operation.
    """
    port, token = kernel

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()
            # Switch to accept_edits — should not raise.
            await client.set_mode(sid, "accept_edits")
            # Switch to auto.
            await client.set_mode(sid, "auto")
            # Switch back to default.
            await client.set_mode(sid, "default")

    _run(_run_test())


def test_set_mode_roundtrip_with_plan(kernel: tuple[int, str]) -> None:
    """Mode can cycle through all 6 values including plan and dont_ask.

    Verifies backward compatibility: plan mode via set_mode("plan") works
    the same as the old set_plan_mode(True) path.
    """
    port, token = kernel

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()
            for mode in (
                "default",
                "plan",
                "bypass",
                "accept_edits",
                "auto",
                "dont_ask",
                "default",
            ):
                await client.set_mode(sid, mode)

    _run(_run_test())
