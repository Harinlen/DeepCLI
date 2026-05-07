"""Phase 2 Probe E2E coverage for the Supervisor router path.

These tests exercise the real process chain:

ProbeClient -> Access Agent /session -> Agent Hub -> Primary Runtime
-> SessionManager -> Orchestrator -> Tools.

The LLM is a local OpenAI-compatible fixture server so the test is
deterministic and does not require external API keys.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ProbeClient,
    ProbeError,
    ToolCallEvent,
    ToolCallUpdate,
    TurnComplete,
    UserChunk,
)
from tests.e2e._home_sandbox import cleanup_test_home, prepare_test_home, token_path_for
from tests.e2e.conftest import KERNEL_DIR, _POLL_INTERVAL_SECS


_PORT = 18240
_STARTUP_TIMEOUT = 45.0
_PROMPT_TIMEOUT = 45.0
_WORKSPACE = Path(tempfile.gettempdir()) / "mustang-phase2-probe-workspace"


def _run(coro: Any, *, timeout: float = _PROMPT_TIMEOUT) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    return asyncio.run(_guarded())


@pytest.fixture(scope="module")
def phase2_kernel() -> Generator[tuple[int, str, Path, Path], None, None]:
    _kill_port_occupants(_PORT)
    fake_llm = _FakeOpenAIServer()
    fake_llm.start()
    sandbox_home = prepare_test_home("phase2-probe")
    token_path = token_path_for(sandbox_home)
    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    (_WORKSPACE / "phase2_fixture.txt").write_text("PHASE2_FILE_CONTENT\n", encoding="utf-8")
    (_WORKSPACE / "phase2_existing.txt").write_text("old content\n", encoding="utf-8")
    (_WORKSPACE / "phase2_alpha.txt").write_text("alpha PHASE2_GREP_TARGET\n", encoding="utf-8")
    (_WORKSPACE / "phase2_beta.log").write_text("beta\n", encoding="utf-8")
    _init_git_workspace(_WORKSPACE)
    _write_probe_skill(sandbox_home)
    _write_probe_hooks(sandbox_home)
    _write_llm_config(sandbox_home, fake_llm.base_url)
    mcp_json_path = KERNEL_DIR / ".mcp.json"
    previous_mcp_json = (
        mcp_json_path.read_text(encoding="utf-8") if mcp_json_path.exists() else None
    )
    mcp_json_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "resources": {
                        "command": sys.executable,
                        "args": [str(Path(__file__).parent / "mcp_resources_server.py")],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    stderr_path = Path(tempfile.gettempdir()) / "mustang-phase2-supervisor.stderr.log"
    stderr_file = stderr_path.open("w")
    env = os.environ.copy()
    env["HOME"] = str(sandbox_home)
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "python",
            "-m",
            "kernel.supervisor",
            "--access-port",
            str(_PORT),
            "--state-dir",
            str(sandbox_home / ".mustang" / "state"),
            "--workspace",
            str(_WORKSPACE),
            "--dev",
            "--prompt-backend",
            "router",
        ],
        cwd=str(KERNEL_DIR),
        stdout=subprocess.DEVNULL,
        stderr=stderr_file,
        env=env,
    )

    try:
        _wait_for_readiness(_PORT, _STARTUP_TIMEOUT)
        if not token_path.exists():
            _dump_stderr(stderr_path)
            raise RuntimeError(f"Auth token not found at {token_path}")
        yield _PORT, token_path.read_text().strip(), _WORKSPACE, sandbox_home
    except Exception:
        _dump_stderr(stderr_path)
        raise
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        stderr_file.close()
        stderr_path.unlink(missing_ok=True)
        fake_llm.stop()
        if previous_mcp_json is None:
            mcp_json_path.unlink(missing_ok=True)
        else:
            mcp_json_path.write_text(previous_mcp_json, encoding="utf-8")
        cleanup_test_home(sandbox_home)


def test_supervisor_readiness_and_auth_rejection(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, _token, _workspace, _home = phase2_kernel
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/access/readiness", timeout=5) as resp:
        payload = json.loads(resp.read())

    assert payload["process_ready"] is True
    assert payload["hub_ready"] is True
    assert payload["default_route_ready"] is True

    async def _bad_token() -> str:
        import websockets

        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/session?token=bad") as ws:
                await ws.recv()
                return "connected"
        except Exception as exc:
            return type(exc).__name__

    assert _run(_bad_token(), timeout=10) != "connected"


def test_probe_session_lifecycle_resume_list_close(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, token, workspace, _home = phase2_kernel

    async def _test() -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            listing = await client.list_sessions(cwd=str(workspace))
            resume = await client.resume_session(sid, cwd=str(workspace))
            close = await client.close_session(sid)
            return sid, listing, resume, close

    sid, listing, resume, close = _run(_test())

    assert any(item["sessionId"] == sid for item in listing["sessions"])
    assert "modes" in resume or "configOptions" in resume
    assert close == {}


def test_probe_session_mode_and_cancel_execution_extensions_route_to_runtime(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, token, workspace, _home = phase2_kernel

    async def _test() -> None:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            await client.set_mode(sid, "accept_edits")
            await client.set_mode(sid, "default")
            await client.cancel_execution(sid)

    _run(_test())


def test_probe_model_and_secret_extension_methods_are_usable(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, token, _workspace, _home = phase2_kernel

    async def _test() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            profiles = await client._request("_mustang.agent/model/profile_list", {})
            providers = await client.list_providers()
            await client._request(
                "_mustang.agent/secrets/auth",
                {
                    "action": "set",
                    "name": "phase2-probe-secret",
                    "value": "phase2-secret-value",
                    "kind": "static",
                },
            )
            listed = await client._request(
                "_mustang.agent/secrets/auth",
                {"action": "list", "kind": "static"},
            )
            masked = await client._request(
                "_mustang.agent/secrets/auth",
                {"action": "get", "name": "phase2-probe-secret"},
            )
            await client._request(
                "_mustang.agent/secrets/auth",
                {"action": "delete", "name": "phase2-probe-secret"},
            )
            return profiles, providers, listed, masked

    profiles, providers, listed, masked = _run(_test())

    assert profiles["profiles"]
    assert any(provider["name"] == "phase2_fake" for provider in providers["providers"])
    assert "phase2-probe-secret" in listed["names"]
    assert masked["value"] != "phase2-secret-value"


def test_probe_model_set_current_updates_existing_session_runtime(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, token, workspace, _home = phase2_kernel

    async def _test() -> tuple[str, str, dict[str, Any]]:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            before = await _collect_text(client, sid, "PHASE2_MODEL_ECHO")
            result = await client._request(
                "_mustang.agent/model/set_current",
                {
                    "role": "default",
                    "provider": "phase2_alt",
                    "model": "phase2-alt-model",
                },
            )
            after = await _collect_text(client, sid, "PHASE2_MODEL_ECHO")
            await client._request(
                "_mustang.agent/model/set_current",
                {
                    "role": "default",
                    "provider": "phase2_fake",
                    "model": "phase2-fake-model",
                },
            )
            return before, after, result

    before, after, result = _run(_test())

    assert result == {
        "role": "default",
        "model": ["phase2_alt", "phase2-alt-model"],
    }
    assert before == "MODEL:phase2-fake-model"
    assert after == "MODEL:phase2-alt-model"


def test_probe_gateway_webhook_route_reports_missing_adapter(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, _token, _workspace, _home = phase2_kernel
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/gateways/missing/webhook",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    else:
        raise AssertionError("expected missing gateway adapter to return 404")


def test_probe_router_prompt_and_client_turn_id_replay(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, token, workspace, _home = phase2_kernel
    client_turn_id = "22222222-2222-4222-8222-222222222222"

    async def _prompt_twice() -> tuple[str, str, list[Any]]:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            first = await _collect_text(client, sid, "Reply with exactly: pong", client_turn_id)
            second = await _collect_text(client, sid, "Reply with exactly: pong", client_turn_id)
            history = await client.load_session(sid, cwd=str(workspace))
            return first, second, history

    first, second, history = _run(_prompt_twice())

    assert first == "pong"
    assert second == "pong"
    assert sum(isinstance(event, UserChunk) for event in history) == 1


def test_probe_router_prompt_retries_transient_llm_disconnect(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, token, workspace, _home = phase2_kernel

    async def _prompt() -> tuple[str, str]:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            text_parts: list[str] = []
            stop_reason = "unknown"
            async for event in client.prompt(
                sid, "PHASE2_TRANSIENT_RETRY", timeout=_PROMPT_TIMEOUT
            ):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
            return "".join(text_parts), stop_reason

    text, stop_reason = _run(_prompt())

    assert text == "PHASE2_TRANSIENT_RETRY_OK"
    assert stop_reason == "end_turn"


def test_probe_file_read_tool_observable_through_router(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, token, workspace, _home = phase2_kernel

    async def _test() -> tuple[str, list[str], list[str]]:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            return await _collect_prompt(
                client,
                sid,
                "PHASE2_FILE_READ: read phase2_fixture.txt and report success.",
            )

    text, tools, updates = _run(_test())

    assert "PHASE2_FILE_READ_OK" in text
    assert "Read" in tools
    assert "completed" in updates


def test_probe_permission_allow_executes_file_write(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, token, workspace, _home = phase2_kernel

    async def _test() -> tuple[str, list[str], bool]:
        saw_permission = False
        text_parts: list[str] = []
        tool_titles: list[str] = []
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            async for event in client.prompt(
                sid,
                "PHASE2_FILE_WRITE_ALLOW: overwrite phase2_existing.txt.",
                timeout=_PROMPT_TIMEOUT,
            ):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_titles.append(event.title)
                elif isinstance(event, PermissionRequest):
                    saw_permission = True
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    assert event.stop_reason == "end_turn"
        return "".join(text_parts), tool_titles, saw_permission

    text, tools, saw_permission = _run(_test())

    assert saw_permission is True
    assert "Write" in tools
    assert "PHASE2_PERMISSION_ALLOWED" in text
    assert (workspace / "phase2_existing.txt").read_text(encoding="utf-8") == "new content\n"


def test_probe_permission_reject_finishes_turn_without_writing(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, token, workspace, _home = phase2_kernel
    target = workspace / "phase2_reject.txt"
    target.unlink(missing_ok=True)

    async def _test() -> tuple[str, bool]:
        saw_permission = False
        text_parts: list[str] = []
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            async for event in client.prompt(
                sid,
                "PHASE2_FILE_WRITE_REJECT: try to write phase2_reject.txt.",
                timeout=_PROMPT_TIMEOUT,
            ):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, PermissionRequest):
                    saw_permission = True
                    await client.reply_permission(event.req_id, "reject")
                elif isinstance(event, TurnComplete):
                    assert event.stop_reason == "end_turn"
        return "".join(text_parts), saw_permission

    text, saw_permission = _run(_test())

    assert saw_permission is True
    assert "PHASE2_PERMISSION_REJECTED" in text
    assert not target.exists()


@pytest.mark.parametrize(
    ("prompt", "expected_tool", "expected_text"),
    [
        ("PHASE2_BASH: run a safe shell command.", "Bash", "PHASE2_BASH_OK"),
        ("PHASE2_PYTHON: run a safe Python snippet.", "Python", "PHASE2_PYTHON_OK"),
        ("PHASE2_TODO: update the todo list.", "TodoWrite", "PHASE2_TODO_OK"),
        ("PHASE2_GLOB: find txt files.", "Glob", "PHASE2_GLOB_OK"),
        ("PHASE2_GREP: search fixture files.", "Grep", "PHASE2_GREP_OK"),
        (
            "PHASE2_SENDMESSAGE_MISSING: route to a missing durable agent.",
            "SendMessage",
            "PHASE2_SENDMESSAGE_ERROR_OK",
        ),
        ("PHASE2_TOOLSEARCH: search for Bash tool.", "ToolSearch", "PHASE2_TOOLSEARCH_OK"),
    ],
)
def test_probe_builtin_tool_matrix_through_router(
    phase2_kernel: tuple[int, str, Path, Path],
    prompt: str,
    expected_tool: str,
    expected_text: str,
) -> None:
    port, token, workspace, _home = phase2_kernel

    async def _test() -> tuple[str, list[str], list[str]]:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            return await _collect_prompt(client, sid, prompt)

    text, tools, updates = _run(_test())

    assert expected_tool in tools
    assert "completed" in updates
    assert expected_text in text


def test_probe_ask_user_question_updated_input_round_trip(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, token, workspace, _home = phase2_kernel

    async def _test() -> tuple[str, list[str], bool]:
        text_parts: list[str] = []
        tool_titles: list[str] = []
        answered = False
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            async for event in client.prompt(
                sid,
                "PHASE2_ASK_USER: ask the user which framework they prefer.",
                timeout=_PROMPT_TIMEOUT,
            ):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_titles.append(event.title)
                elif isinstance(event, PermissionRequest):
                    if event.tool_input is not None and "questions" in event.tool_input:
                        answered = True
                        await client.reply_permission(
                            event.req_id,
                            "allow_once",
                            updated_input={
                                "questions": event.tool_input["questions"],
                                "answers": {"Which framework do you prefer?": "React"},
                            },
                        )
                    else:
                        await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    assert event.stop_reason == "end_turn"
        return "".join(text_parts), tool_titles, answered

    text, tools, answered = _run(_test())

    assert answered is True
    assert any(title in {"AskUserQuestion", "Ask user"} for title in tools)
    assert "PHASE2_ASK_USER_OK" in text
    assert "React" in text


@pytest.mark.parametrize(
    ("prompt", "expected_tool", "expected_text"),
    [
        (
            "PHASE2_MCP_ECHO: call the resources MCP echo tool.",
            "resources/echo",
            "PHASE2_MCP_ECHO_OK",
        ),
        (
            "PHASE2_MCP_LIST: list resources from the resources MCP server.",
            "ListMcpResources",
            "PHASE2_MCP_LIST_OK",
        ),
        (
            "PHASE2_MCP_READ: read config://app/settings from MCP.",
            "ReadMcpResource",
            "PHASE2_MCP_READ_OK",
        ),
    ],
)
def test_probe_mcp_tool_and_resource_matrix(
    phase2_kernel: tuple[int, str, Path, Path],
    prompt: str,
    expected_tool: str,
    expected_text: str,
) -> None:
    port, token, workspace, _home = phase2_kernel

    async def _test() -> tuple[str, list[str], list[str]]:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            return await _collect_prompt(client, sid, prompt)

    text, tools, updates = _run(_test())

    assert expected_tool in tools
    assert "completed" in updates
    assert expected_text in text


@pytest.mark.parametrize(
    ("prompt", "expected_tool", "expected_text"),
    [
        ("PHASE2_SKILL: invoke the phase2 probe skill.", "Skill", "PHASE2_SKILL_OK"),
        ("PHASE2_MEMORY_WRITE: write a probe memory.", "memory_write", "PHASE2_MEMORY_WRITE_OK"),
        (
            "PHASE2_MEMORY_APPEND: append to the probe memory.",
            "memory_append",
            "PHASE2_MEMORY_APPEND_OK",
        ),
        ("PHASE2_MEMORY_LIST: list probe memories.", "memory_list", "PHASE2_MEMORY_LIST_OK"),
        ("PHASE2_CRON_CREATE: create a probe cron job.", "CronCreate", "PHASE2_CRON_CREATE_OK"),
        ("PHASE2_CRON_LIST: list probe cron jobs.", "CronList", "PHASE2_CRON_LIST_OK"),
        (
            "PHASE2_CRON_DELETE_MISSING: delete a missing probe cron job.",
            "CronDelete",
            "PHASE2_CRON_DELETE_OK",
        ),
    ],
)
def test_probe_subsystem_tool_matrix_through_router(
    phase2_kernel: tuple[int, str, Path, Path],
    prompt: str,
    expected_tool: str,
    expected_text: str,
) -> None:
    port, token, workspace, _home = phase2_kernel

    async def _test() -> tuple[str, list[str], list[str]]:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            return await _collect_prompt(client, sid, prompt)

    text, tools, updates = _run(_test())

    assert expected_tool in tools
    assert "completed" in updates
    assert expected_text in text


def test_probe_hook_user_prompt_submit_fires_in_runtime(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, token, workspace, home = phase2_kernel
    sentinel = home / ".mustang" / "phase2-hook-fired.txt"
    sentinel.unlink(missing_ok=True)

    async def _test() -> None:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            await _collect_text(client, sid, "PHASE2_HOOK_PROMPT")

    _run(_test())

    assert sentinel.read_text(encoding="utf-8") == "PHASE2_HOOK_PROMPT"


def test_probe_git_context_from_workspace_reaches_llm(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, token, workspace, _home = phase2_kernel
    if not (workspace / ".git").exists():
        pytest.skip("git binary is unavailable in this environment")

    async def _test() -> str:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            return await _collect_text(client, sid, "PHASE2_GIT_CONTEXT")

    text = _run(_test())

    assert text == "PHASE2_GIT_CONTEXT_OK"


def test_probe_shell_and_python_extension_methods_emit_execution_updates(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, token, workspace, _home = phase2_kernel

    async def _test() -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            shell = await client.execute_shell(
                sid,
                "printf PHASE2_EXEC_SHELL",
                timeout=_PROMPT_TIMEOUT,
            )
            python = await client.execute_python(
                sid,
                "print('PHASE2_EXEC_PYTHON')",
                timeout=_PROMPT_TIMEOUT,
            )
            events = await client.drain_events(timeout=0.2)
            texts = [
                event.text
                for event in events
                if getattr(event, "stream", None) in {"stdout", "stderr"}
            ]
            return shell, python, texts

    shell, python, texts = _run(_test())

    assert shell["exitCode"] == 0
    assert python["exitCode"] == 0
    assert any("PHASE2_EXEC_SHELL" in text for text in texts)
    assert any("PHASE2_EXEC_PYTHON" in text for text in texts)


def test_probe_error_propagation_for_bad_raw_method(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, token, _workspace, _home = phase2_kernel

    async def _test() -> ProbeError:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            try:
                await client._request("phase2/no_such_method", {})
            except ProbeError as exc:
                return exc
        raise AssertionError("expected ProbeError")

    error = _run(_test())

    assert error.code == -32601
    assert "Method not found" in error.rpc_message
    assert "Internal error" not in error.rpc_message


def test_probe_test_mode_emits_machine_readable_json(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    port, _token, workspace, home = phase2_kernel
    env = os.environ.copy()
    env["HOME"] = str(home)

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "probe",
            "--port",
            str(port),
            "--cwd",
            str(workspace),
            "--test",
            "--prompt",
            "Reply with exactly: pong",
        ],
        cwd=str(KERNEL_DIR.parent / "probe"),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert payload["promptCompleted"] is True
    assert payload["stopReason"] == "end_turn"
    assert payload["text"] == "pong"
    assert payload["errors"] == []


async def _collect_text(
    client: ProbeClient,
    session_id: str,
    prompt: str,
    client_turn_id: str | None = None,
) -> str:
    text_parts: list[str] = []
    async for event in client.prompt(
        session_id,
        prompt,
        client_turn_id=client_turn_id,
        timeout=_PROMPT_TIMEOUT,
    ):
        if isinstance(event, AgentChunk):
            text_parts.append(event.text)
        elif isinstance(event, PermissionRequest):
            await client.reply_permission(event.req_id, "allow_once")
        elif isinstance(event, TurnComplete):
            assert event.stop_reason == "end_turn"
    return "".join(text_parts)


async def _collect_prompt(
    client: ProbeClient,
    session_id: str,
    prompt: str,
) -> tuple[str, list[str], list[str]]:
    text_parts: list[str] = []
    tool_titles: list[str] = []
    update_statuses: list[str] = []
    async for event in client.prompt(session_id, prompt, timeout=_PROMPT_TIMEOUT):
        if isinstance(event, AgentChunk):
            text_parts.append(event.text)
        elif isinstance(event, ToolCallEvent):
            tool_titles.append(event.title)
        elif isinstance(event, ToolCallUpdate):
            update_statuses.append(event.status)
        elif isinstance(event, PermissionRequest):
            await client.reply_permission(event.req_id, "allow_once")
        elif isinstance(event, TurnComplete):
            assert event.stop_reason == "end_turn"
    return "".join(text_parts), tool_titles, update_statuses


class _FakeOpenAIServer:
    def __init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address
        return f"http://{host}:{port}/v1"

    def start(self) -> None:
        _FakeOpenAIHandler._transient_attempts.clear()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    _transient_attempts: dict[str, int] = {}

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.endswith("/models"):
            self._json({"data": [{"id": "phase2-fake-model"}, {"id": "phase2-alt-model"}]})
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.endswith("/chat/completions"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        user_text = _last_user_text(body)
        if "PHASE2_TRANSIENT_RETRY" in user_text:
            attempts = self._transient_attempts.get("PHASE2_TRANSIENT_RETRY", 0)
            self._transient_attempts["PHASE2_TRANSIENT_RETRY"] = attempts + 1
            if attempts == 0:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b'40\r\ndata: {"choices":[{"delta":{"content":"')
                self.wfile.flush()
                self.close_connection = True
                return
        response = _script_response(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for payload in response:
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _json(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _script_response(body: dict[str, Any]) -> list[dict[str, Any]]:
    body_text = json.dumps(body, ensure_ascii=False)
    user_text = _last_user_text(body)
    if "PHASE2_MODEL_ECHO" in user_text:
        return _text_chunks(f"MODEL:{body.get('model')}")
    if _has_tool_result(body):
        if "PHASE2_BASH" in user_text:
            return _text_chunks("PHASE2_BASH_OK")
        if "PHASE2_PYTHON" in user_text:
            return _text_chunks("PHASE2_PYTHON_OK")
        if "PHASE2_TODO" in user_text:
            return _text_chunks("PHASE2_TODO_OK")
        if "PHASE2_GLOB" in user_text:
            return _text_chunks("PHASE2_GLOB_OK")
        if "PHASE2_GREP" in user_text:
            return _text_chunks("PHASE2_GREP_OK")
        if "PHASE2_WEBFETCH_BLOCKED" in user_text:
            return _text_chunks("PHASE2_WEBFETCH_BLOCKED_OK")
        if "PHASE2_SENDMESSAGE_MISSING" in user_text:
            return _text_chunks("PHASE2_SENDMESSAGE_ERROR_OK")
        if "PHASE2_TOOLSEARCH" in user_text:
            return _text_chunks("PHASE2_TOOLSEARCH_OK")
        if "PHASE2_ASK_USER" in user_text:
            return _text_chunks("PHASE2_ASK_USER_OK React")
        if "PHASE2_MCP_ECHO" in user_text:
            return _text_chunks("PHASE2_MCP_ECHO_OK")
        if "PHASE2_MCP_LIST" in user_text:
            return _text_chunks("PHASE2_MCP_LIST_OK")
        if "PHASE2_MCP_READ" in user_text:
            return _text_chunks("PHASE2_MCP_READ_OK")
        if "PHASE2_SKILL" in user_text:
            return _text_chunks("PHASE2_SKILL_OK")
        if "PHASE2_MEMORY_WRITE" in user_text:
            return _text_chunks("PHASE2_MEMORY_WRITE_OK")
        if "PHASE2_MEMORY_APPEND" in user_text:
            return _text_chunks("PHASE2_MEMORY_APPEND_OK")
        if "PHASE2_MEMORY_LIST" in user_text:
            return _text_chunks("PHASE2_MEMORY_LIST_OK")
        if "PHASE2_CRON_CREATE" in user_text:
            return _text_chunks("PHASE2_CRON_CREATE_OK")
        if "PHASE2_CRON_LIST" in user_text:
            return _text_chunks("PHASE2_CRON_LIST_OK")
        if "PHASE2_CRON_DELETE_MISSING" in user_text:
            return _text_chunks("PHASE2_CRON_DELETE_OK")
        if "phase2_fixture.txt" in body_text:
            return _text_chunks("PHASE2_FILE_READ_OK")
        if "phase2_reject.txt" in body_text:
            return _text_chunks("PHASE2_PERMISSION_REJECTED")
        if "phase2_existing.txt" in body_text:
            return _text_chunks("PHASE2_PERMISSION_ALLOWED")
        return _text_chunks("PHASE2_TOOL_DONE")
    if "PHASE2_TRANSIENT_RETRY" in user_text:
        return _text_chunks("PHASE2_TRANSIENT_RETRY_OK")
    if "PHASE2_FILE_READ" in user_text:
        return _tool_call(
            "call_file_read",
            "Read",
            {"file_path": "phase2_fixture.txt"},
        )
    if "PHASE2_FILE_WRITE_ALLOW" in user_text:
        return _tool_call(
            "call_file_write",
            "Write",
            {"file_path": "phase2_existing.txt", "content": "new content\n"},
        )
    if "PHASE2_FILE_WRITE_REJECT" in user_text:
        return _tool_call(
            "call_file_write_reject",
            "Write",
            {"file_path": "phase2_reject.txt", "content": "should not exist\n"},
        )
    if "PHASE2_BASH" in user_text:
        return _tool_call("call_bash", "Bash", {"command": "printf PHASE2_BASH_STDOUT"})
    if "PHASE2_PYTHON" in user_text:
        return _tool_call("call_python", "Python", {"code": "print('PHASE2_PYTHON_STDOUT')"})
    if "PHASE2_TODO" in user_text:
        return _tool_call(
            "call_todo",
            "TodoWrite",
            {
                "todos": [
                    {
                        "content": "Verify Probe todo coverage",
                        "activeForm": "Verifying Probe todo coverage",
                        "status": "in_progress",
                    }
                ]
            },
        )
    if "PHASE2_GLOB" in user_text:
        return _tool_call("call_glob", "Glob", {"pattern": "*.txt", "path": "."})
    if "PHASE2_GREP" in user_text:
        return _tool_call("call_grep", "Grep", {"pattern": "PHASE2_GREP_TARGET", "path": "."})
    if "PHASE2_WEBFETCH_BLOCKED" in user_text:
        return _tool_call(
            "call_webfetch",
            "WebFetch",
            {"url": "http://127.0.0.1:1/blocked", "max_chars": 2000},
        )
    if "PHASE2_SENDMESSAGE_MISSING" in user_text:
        return _tool_call(
            "call_sendmessage",
            "SendMessage",
            {"to": "agent:missing-agent", "message": "hello"},
        )
    if "PHASE2_TOOLSEARCH" in user_text:
        return _tool_call("call_toolsearch", "ToolSearch", {"query": "select:Bash"})
    if "PHASE2_ASK_USER" in user_text:
        return _tool_call(
            "call_ask_user",
            "AskUserQuestion",
            {
                "questions": [
                    {
                        "header": "Framework",
                        "question": "Which framework do you prefer?",
                        "type": "choice",
                        "options": [
                            {"label": "React", "description": "A JavaScript library"},
                            {"label": "Vue", "description": "A progressive framework"},
                        ],
                    }
                ]
            },
        )
    if "PHASE2_MCP_ECHO" in user_text:
        return _tool_call(
            "call_mcp_echo",
            "mcp__resources__echo",
            {"message": "hello from phase2 probe"},
        )
    if "PHASE2_MCP_LIST" in user_text:
        return _tool_call(
            "call_mcp_list",
            "ListMcpResources",
            {"server": "resources"},
        )
    if "PHASE2_MCP_READ" in user_text:
        return _tool_call(
            "call_mcp_read",
            "ReadMcpResource",
            {"server": "resources", "uri": "config://app/settings"},
        )
    if "PHASE2_SKILL" in user_text:
        return _tool_call("call_skill", "Skill", {"skill": "phase2-probe", "args": "probe"})
    if "PHASE2_MEMORY_WRITE" in user_text:
        return _tool_call(
            "call_memory_write",
            "memory_write",
            {
                "name": "phase2-probe-memory",
                "category": "semantic",
                "description": "Phase 2 Probe memory subsystem verification.",
                "content": "PHASE2_MEMORY_CONTENT",
            },
        )
    if "PHASE2_MEMORY_APPEND" in user_text:
        return _tool_call(
            "call_memory_append",
            "memory_append",
            {"name": "phase2-probe-memory", "content": "\nPHASE2_MEMORY_APPENDED"},
        )
    if "PHASE2_MEMORY_LIST" in user_text:
        return _tool_call("call_memory_list", "memory_list", {"category": "semantic"})
    if "PHASE2_CRON_CREATE" in user_text:
        return _tool_call(
            "call_cron_create",
            "CronCreate",
            {
                "schedule": "every 1h",
                "prompt": "PHASE2_CRON_PROMPT",
                "description": "Phase 2 Probe cron job",
                "durable": False,
                "delivery": "none",
            },
        )
    if "PHASE2_CRON_LIST" in user_text:
        return _tool_call("call_cron_list", "CronList", {"include_completed": True})
    if "PHASE2_CRON_DELETE_MISSING" in user_text:
        return _tool_call("call_cron_delete", "CronDelete", {"id": "missing-phase2-cron"})
    if "PHASE2_HOOK_MUTATED" in body_text:
        return _text_chunks("PHASE2_HOOK_OK")
    if "PHASE2_GIT_CONTEXT" in user_text and "Current branch:" in body_text:
        return _text_chunks("PHASE2_GIT_CONTEXT_OK")
    if "PHASE2_GIT_CONTEXT" in user_text:
        return _text_chunks("PHASE2_GIT_CONTEXT_MISSING")
    return _text_chunks("pong")


def _has_tool_result(body: dict[str, Any]) -> bool:
    for message in body.get("messages") or []:
        if message.get("role") == "tool":
            return True
    return False


def _last_user_text(body: dict[str, Any]) -> str:
    for message in reversed(body.get("messages") or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            return "".join(parts)
    return ""


def _text_chunks(text: str) -> list[dict[str, Any]]:
    return [
        {
            "choices": [
                {
                    "delta": {"content": text},
                    "finish_reason": None,
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(arguments),
                                },
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]


def _write_probe_skill(home: Path) -> None:
    skill_dir = home / ".mustang" / "skills" / "phase2-probe"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: phase2-probe\n"
        "description: Probe fixture skill for subsystem availability.\n"
        "when_to_use: When PHASE2_SKILL is requested.\n"
        "---\n"
        "# Phase 2 Probe Skill\n\n"
        "PHASE2_SKILL_BODY $ARGUMENTS\n",
        encoding="utf-8",
    )


def _write_probe_hooks(home: Path) -> None:
    hook_dir = home / ".mustang" / "hooks" / "phase2-prompt-rewrite"
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "HOOK.md").write_text(
        "---\n"
        "name: phase2-prompt-rewrite\n"
        "description: Probe fixture hook for prompt mutation.\n"
        "events: [user_prompt_submit]\n"
        "---\n"
        "# Phase 2 prompt rewrite\n",
        encoding="utf-8",
    )
    (hook_dir / "handler.py").write_text(
        "import os\n"
        "from pathlib import Path\n\n"
        "def handle(ctx):\n"
        "    if ctx.user_text and 'PHASE2_HOOK_PROMPT' in ctx.user_text:\n"
        "        path = Path(os.environ['HOME']) / '.mustang' / 'phase2-hook-fired.txt'\n"
        "        path.write_text(ctx.user_text, encoding='utf-8')\n"
        "        ctx.user_text = ctx.user_text.replace(\n"
        "            'PHASE2_HOOK_PROMPT', 'PHASE2_HOOK_MUTATED'\n"
        "        )\n"
        "        ctx.messages.append('PHASE2_HOOK_REMINDER')\n",
        encoding="utf-8",
    )


def _init_git_workspace(workspace: Path) -> None:
    if shutil.which("git") is None:
        return
    if (workspace / ".git").exists():
        return
    subprocess.run(["git", "init"], cwd=workspace, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "phase2@example.invalid"],
        cwd=workspace,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Phase2 Probe"],
        cwd=workspace,
        capture_output=True,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=workspace, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "phase2 probe fixture"],
        cwd=workspace,
        capture_output=True,
        check=True,
    )


def _write_llm_config(home: Path, fake_base_url: str) -> None:
    config = {
        "llm": {
            "providers": {
                "phase2_fake": {
                    "type": "openai_compatible",
                    "base_url": fake_base_url,
                    "api_key": "phase2-test",
                    "models": ["phase2-fake-model"],
                },
                "phase2_alt": {
                    "type": "openai_compatible",
                    "base_url": fake_base_url,
                    "api_key": "phase2-test",
                    "models": ["phase2-alt-model"],
                },
            },
            "current_used": {
                "default": ["phase2_fake", "phase2-fake-model"],
                "compact": ["phase2_fake", "phase2-fake-model"],
            },
        }
    }
    path = home / ".mustang" / "config" / "kernel.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _wait_for_readiness(port: int, timeout: float) -> None:
    url = f"http://127.0.0.1:{port}/access/readiness"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                payload = json.loads(resp.read())
            if (
                payload.get("process_ready")
                and payload.get("hub_ready")
                and payload.get("default_route_ready")
            ):
                return
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            pass
        time.sleep(_POLL_INTERVAL_SECS)
    raise RuntimeError(f"Supervisor Access Agent on port {port} did not become ready")


def _kill_port_occupants(port: int) -> None:
    try:
        out = subprocess.check_output(
            ["lsof", "-ti", f":{port}"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return
    for pid_str in out.splitlines():
        try:
            pid = int(pid_str.strip())
            if pid != os.getpid():
                os.kill(pid, signal.SIGKILL)
        except (ValueError, ProcessLookupError, PermissionError):
            pass


def _dump_stderr(path: Path) -> None:
    try:
        text = path.read_text()
    except OSError:
        return
    if text.strip():
        print(f"\n=== Phase 2 supervisor stderr ===\n{text}\n=== End stderr ===")
