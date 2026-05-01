"""Hook runner — executes hooks by type (command, prompt, http).

Each hook type has a dedicated executor.  The public API is
:func:`run_hooks`, which runs a list of hooks sequentially and
returns a combined :class:`HookResult`.

Design principles:
  - ``pre_tool_use`` + ``command`` type: non-zero exit code → block.
  - All other combinations: fail-open (log warning, don't block).
  - ``async_`` hooks fire-and-forget (background task, no await).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

# Track background hook tasks to prevent GC and allow cleanup on shutdown.
_background_hook_tasks: set[asyncio.Task[None]] = set()

import httpx

from daemon.extensions.hooks.base import (
    HookConfig,
    HookContext,
    HookEvent,
    HookResult,
    HookType,
)

logger = logging.getLogger(__name__)

# Environment variable interpolation pattern: $VAR_NAME
_ENV_VAR_PREFIX = "$"


def _interpolate_env(value: str) -> str:
    """Replace ``$VAR_NAME`` references with environment variable values.

    Only replaces tokens that start with ``$`` and consist of
    alphanumeric characters plus underscores.  Unknown variables
    are left as-is.

    Args:
        value: String potentially containing ``$VAR_NAME`` references.

    Returns:
        String with environment variables expanded.
    """
    result: list[str] = []
    i = 0
    while i < len(value):
        if (
            value[i] == _ENV_VAR_PREFIX
            and i + 1 < len(value)
            and (value[i + 1].isalpha() or value[i + 1] == "_")
        ):
            # Read the variable name
            j = i + 1
            while j < len(value) and (value[j].isalnum() or value[j] == "_"):
                j += 1
            var_name = value[i + 1 : j]
            env_val = os.environ.get(var_name)
            if env_val is not None:
                result.append(env_val)
            else:
                result.append(value[i:j])  # leave as-is
            i = j
        else:
            result.append(value[i])
            i += 1
    return "".join(result)


def _build_hook_env(ctx: HookContext) -> dict[str, str]:
    """Build environment variables for command hooks.

    Injects context fields into the subprocess environment alongside
    inherited env vars.  All non-None string/numeric fields are
    exported as ``HOOK_<FIELD>`` or legacy ``TOOL_*`` names.

    Args:
        ctx: Hook execution context.

    Returns:
        Environment dict for subprocess.
    """
    env = dict(os.environ)
    # Legacy tool variables (backward compat)
    if ctx.tool_name is not None:
        env["TOOL_NAME"] = ctx.tool_name
    env["TOOL_INPUT_JSON"] = json.dumps(ctx.tool_input)
    if ctx.tool_output is not None:
        env["TOOL_OUTPUT"] = ctx.tool_output
    # New context variables
    if ctx.error_message is not None:
        env["HOOK_ERROR_MESSAGE"] = ctx.error_message
    if ctx.session_id is not None:
        env["HOOK_SESSION_ID"] = ctx.session_id
    if ctx.cwd is not None:
        env["HOOK_CWD"] = ctx.cwd
    if ctx.is_resume is not None:
        env["HOOK_IS_RESUME"] = "1" if ctx.is_resume else "0"
    if ctx.duration_s is not None:
        env["HOOK_DURATION_S"] = str(ctx.duration_s)
    if ctx.user_text is not None:
        env["HOOK_USER_TEXT"] = ctx.user_text
    if ctx.message_count is not None:
        env["HOOK_MESSAGE_COUNT"] = str(ctx.message_count)
    if ctx.token_estimate is not None:
        env["HOOK_TOKEN_ESTIMATE"] = str(ctx.token_estimate)
    if ctx.messages_removed is not None:
        env["HOOK_MESSAGES_REMOVED"] = str(ctx.messages_removed)
    if ctx.file_path is not None:
        env["HOOK_FILE_PATH"] = ctx.file_path
    if ctx.change_type is not None:
        env["HOOK_CHANGE_TYPE"] = ctx.change_type
    if ctx.agent_description is not None:
        env["HOOK_AGENT_DESCRIPTION"] = ctx.agent_description
    if ctx.depth is not None:
        env["HOOK_DEPTH"] = str(ctx.depth)
    return env


def _try_parse_json_result(stdout: bytes | None) -> dict[str, Any] | None:
    """Attempt to parse structured JSON from command hook stdout.

    If stdout is valid JSON containing recognized keys (``blocked``,
    ``modified_input``, ``permission``), returns the parsed dict.
    Otherwise returns None (non-JSON output is normal).
    """
    if not stdout:
        return None
    text = stdout.decode(errors="replace").strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    # Only return if it contains at least one recognized key
    _RECOGNIZED_KEYS = {"blocked", "modified_input", "permission", "output"}
    if data.keys() & _RECOGNIZED_KEYS:
        return data
    return None


async def _run_command_hook(hook: HookConfig, ctx: HookContext) -> HookResult:
    """Execute a command-type hook via subprocess.

    Non-zero exit code on ``pre_tool_use`` events means "block".
    Timeout and other errors are fail-open (logged, not blocking).

    Args:
        hook: The hook config (must have ``command`` set).
        ctx: Hook execution context.

    Returns:
        HookResult with blocked=True if exit code != 0 on pre_tool_use.
    """
    if not hook.command:
        logger.warning("Command hook has no command, skipping")
        return HookResult()

    env = _build_hook_env(ctx)

    try:
        proc = await asyncio.create_subprocess_shell(
            hook.command,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=hook.timeout)
    except asyncio.TimeoutError:
        logger.warning("Command hook timed out after %ds: %s", hook.timeout, hook.command)
        return HookResult()
    except OSError as exc:
        logger.warning("Command hook failed to start: %s — %s", hook.command, exc)
        return HookResult()

    output = (stdout or b"").decode(errors="replace").strip()
    if stderr:
        output += "\n" + stderr.decode(errors="replace").strip()

    # Try to parse structured JSON response from stdout
    structured = _try_parse_json_result(stdout)

    # Non-zero exit on blocking events → block
    _BLOCKABLE_EVENTS = {HookEvent.PRE_TOOL_USE, HookEvent.USER_PROMPT_SUBMIT}
    if proc.returncode != 0 and hook.event in _BLOCKABLE_EVENTS:
        logger.info(
            "Hook blocked execution (exit %d): %s",
            proc.returncode,
            hook.command,
        )
        return HookResult(
            blocked=True,
            output=output,
            modified_input=structured.get("modified_input") if structured else None,
            permission=structured.get("permission") if structured else None,
        )

    return HookResult(
        output=output if output else None,
        modified_input=structured.get("modified_input") if structured else None,
        permission=structured.get("permission") if structured else None,
    )


async def _run_prompt_hook(hook: HookConfig, ctx: HookContext) -> HookResult:
    """Execute a prompt-type hook by evaluating with an LLM.

    Phase 3 stub: logs a warning and returns a no-op result.
    Full implementation requires ProviderRegistry injection
    (deferred to keep the runner stateless in Phase 3).

    Args:
        hook: The hook config (must have ``prompt_text`` set).
        ctx: Hook execution context.

    Returns:
        HookResult (always non-blocking in Phase 3).
    """
    # Prompt hooks need provider access — Phase 3 logs and skips
    logger.warning(
        "Prompt hook not fully implemented in Phase 3, skipping: %s",
        hook.prompt_text,
    )
    return HookResult()


async def _run_http_hook(hook: HookConfig, ctx: HookContext) -> HookResult:
    """Execute an HTTP-type hook by POSTing to an external URL.

    Headers support ``$ENV_VAR`` interpolation.  The request body
    is a JSON object with the hook context.  Failures are fail-open.

    Args:
        hook: The hook config (must have ``url`` set).
        ctx: Hook execution context.

    Returns:
        HookResult (always non-blocking).
    """
    if not hook.url:
        logger.warning("HTTP hook has no URL, skipping")
        return HookResult()

    # Build headers with env var interpolation
    headers: dict[str, str] = {}
    for k, v in hook.headers.items():
        headers[k] = _interpolate_env(v)
    headers.setdefault("Content-Type", "application/json")

    # Build body
    if hook.body:
        body_str = _interpolate_env(hook.body)
    else:
        body_data: dict[str, Any] = {
            "event": hook.event.value,
        }
        if ctx.tool_name is not None:
            body_data["tool_name"] = ctx.tool_name
            body_data["tool_input"] = ctx.tool_input
        if ctx.tool_output is not None:
            body_data["tool_output"] = ctx.tool_output
        body_str = json.dumps(body_data)

    try:
        async with httpx.AsyncClient(timeout=hook.timeout) as client:
            resp = await client.post(hook.url, content=body_str, headers=headers)
        return HookResult(output=f"HTTP {resp.status_code}")
    except httpx.HTTPError as exc:
        logger.warning("HTTP hook failed: %s — %s", hook.url, exc)
        return HookResult()
    except Exception as exc:
        logger.warning("HTTP hook unexpected error: %s — %s", hook.url, exc)
        return HookResult()


# Executor dispatch table
_EXECUTORS = {
    HookType.COMMAND: _run_command_hook,
    HookType.PROMPT: _run_prompt_hook,
    HookType.HTTP: _run_http_hook,
}


async def run_hook(hook: HookConfig, ctx: HookContext) -> HookResult:
    """Execute a single hook.

    If the hook has ``async_=True``, it is launched as a background
    task and returns immediately with a non-blocking result.

    Args:
        hook: The hook to execute.
        ctx: Hook execution context.

    Returns:
        HookResult from the executor.
    """
    executor = _EXECUTORS.get(hook.type)
    if executor is None:
        logger.warning("Unknown hook type: %s", hook.type)
        return HookResult()

    if hook.async_:
        # Launch in background; store task ref to prevent GC and allow
        # shutdown cleanup.
        task = asyncio.create_task(_run_async_wrapper(hook, executor, ctx))
        _background_hook_tasks.discard(None)  # no-op, just to reference the set
        _background_hook_tasks.add(task)
        task.add_done_callback(_background_hook_tasks.discard)
        return HookResult()

    return await executor(hook, ctx)


async def _run_async_wrapper(
    hook: HookConfig,
    executor: Any,
    ctx: HookContext,
) -> None:
    """Wrapper for background hook execution with error handling.

    Ensures background hooks don't crash silently.

    Args:
        hook: The hook being executed.
        executor: The async executor function.
        ctx: Hook execution context.
    """
    try:
        await executor(hook, ctx)
    except Exception:
        logger.exception("Background hook failed: %s", hook.type.value)


async def run_hooks(hooks: list[HookConfig], ctx: HookContext) -> HookResult:
    """Execute a list of hooks sequentially.

    Stops early if any hook blocks (returns ``blocked=True``).
    Output from all executed hooks is concatenated.

    Args:
        hooks: Hooks to execute in order.
        ctx: Hook execution context.

    Returns:
        Combined HookResult.  ``blocked=True`` if any hook blocked.
    """
    if not hooks:
        return HookResult()

    outputs: list[str] = []
    last_modified_input: dict[str, Any] | None = None
    last_permission: str | None = None
    for hook in hooks:
        result = await run_hook(hook, ctx)
        if result.output:
            outputs.append(result.output)
        if result.modified_input is not None:
            last_modified_input = result.modified_input
        if result.permission is not None:
            last_permission = result.permission
        if result.blocked:
            return HookResult(
                blocked=True,
                output="\n".join(outputs) if outputs else None,
                modified_input=last_modified_input,
                permission=last_permission,
            )

    return HookResult(
        output="\n".join(outputs) if outputs else None,
        modified_input=last_modified_input,
        permission=last_permission,
    )
