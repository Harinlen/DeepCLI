"""Interactive REPL for deepcli-probe.

Reads user input with the built-in ``input()`` call (no readline magic,
no curses) and prints the kernel's response as it streams in.
"""

from __future__ import annotations

import asyncio
import shlex
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .client import (
    AgentChunk,
    PermissionRequest,
    ProbeClient,
    TurnComplete,
    ToolCallEvent,
    ToolCallUpdate,
    UserChunk,
)

if TYPE_CHECKING:
    from .client import Event

_HELP = """\
Commands:
  /exit                quit
  /cancel              cancel the current turn
  /session             print the active session ID
  /mode <name>         switch permission mode (default/plan/bypass/accept_edits/auto/dont_ask)

  /provider list                                list all providers and models
  /provider add <name> <type> [options]         add a provider
    options: --api-key, --base-url, --aws-secret-key, --aws-region, --models m1,m2
  /provider remove <name>                       remove a provider
  /provider refresh <name>                      re-discover models for a provider

  /model default <provider> <model_id>          set the default model
  /model list                                   list all available models

  /auth set <name> <value> [--kind static|bearer]   store a secret
  /auth get <name>                                   show a secret (masked)
  /auth list [--kind static|bearer|oauth]            list secret names
  /auth delete <name>                                delete a secret
  /auth import-env <ENV_VAR> <name>                  import from env var

  /help                show this message
"""


async def run_repl(client: ProbeClient, session_id: str) -> None:
    """Run the interactive read-eval-print loop.

    Blocks until the user types ``/exit`` or sends EOF (Ctrl-D).
    Uses a single-thread executor so ``input()`` doesn't block the event loop.

    Args:
        client: Connected and initialized ``ProbeClient``.
        session_id: The active session ID to send prompts to.
    """
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="probe-input")
    loop = asyncio.get_running_loop()
    launched_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    print(f"[probe] session {session_id}")
    print(f"[probe] Launched at {launched_at}")
    print("[probe] /help for commands, /exit to quit\n")

    async def read_line(prompt: str) -> str:
        """Read a line from stdin without blocking the event loop."""
        return await loop.run_in_executor(executor, input, prompt)

    try:
        while True:
            try:
                line = await read_line("You> ")
            except EOFError:
                # Ctrl-D -- exit cleanly
                break
            except asyncio.CancelledError:
                # Ctrl-C at the prompt -- exit cleanly without a traceback.
                # Newline so the next shell prompt starts on a fresh line.
                print()
                break

            line = line.strip()
            if not line:
                continue

            if line == "/exit":
                break
            if line == "/cancel":
                await client.cancel(session_id)
                print("[cancelled]")
                continue
            if line == "/session":
                print(f"[session: {session_id}]")
                continue
            if line.startswith("/mode"):
                parts = line.split(None, 1)
                if len(parts) < 2:
                    print("[usage: /mode <default|plan|bypass|accept_edits|auto|dont_ask>]")
                    continue
                mode_id = parts[1].strip()
                try:
                    await client.set_mode(session_id, mode_id)
                    print(f"[mode: {mode_id}]")
                except Exception as exc:
                    print(f"[mode error: {exc}]")
                continue
            if line.startswith("/provider"):
                await _handle_provider_command(client, line)
                continue
            if line.startswith("/model"):
                await _handle_model_command(client, line)
                continue
            if line.startswith("/auth"):
                await _handle_auth_command(client, line)
                continue
            if line == "/help":
                print(_HELP)
                continue

            print("Agent> ", end="", flush=True)
            try:
                async for event in client.prompt(session_id, line):
                    await _handle_event(event, client, loop, executor)
            except Exception as exc:
                print(f"\n[error: {exc}]")
    finally:
        executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Provider / model commands
# ---------------------------------------------------------------------------


async def _handle_provider_command(client: ProbeClient, line: str) -> None:
    """Dispatch ``/provider <subcommand> ...``."""
    try:
        tokens = shlex.split(line)
    except ValueError as exc:
        print(f"[parse error: {exc}]")
        return

    if len(tokens) < 2:
        print("[usage: /provider list|add|remove|refresh]")
        return

    sub = tokens[1]

    if sub == "list":
        try:
            result = await client.list_providers()
            providers = result.get("providers", [])
            default = result.get("defaultModel", [])
            if not providers:
                print("[no providers configured]")
                return
            print("[providers]")
            for p in providers:
                name = p.get("name", "?")
                ptype = p.get("providerType", "?")
                models = p.get("models", [])
                print(f"  {name} ({ptype})")
                for m in models:
                    marker = ""
                    if default == [name, m]:
                        marker = " <- default"
                    print(f"    - {m}{marker}")
        except Exception as exc:
            print(f"[error: {exc}]")

    elif sub == "add":
        if len(tokens) < 4:
            print(
                "[usage: /provider add <name> <type> [--api-key KEY] [--base-url URL] "
                "[--aws-secret-key KEY] [--aws-region REGION] [--models m1,m2,...]]"
            )
            return
        name = tokens[2]
        ptype = tokens[3]
        kwargs: dict = {}
        i = 4
        while i < len(tokens):
            if tokens[i] == "--api-key" and i + 1 < len(tokens):
                kwargs["api_key"] = tokens[i + 1]
                i += 2
            elif tokens[i] == "--base-url" and i + 1 < len(tokens):
                kwargs["base_url"] = tokens[i + 1]
                i += 2
            elif tokens[i] == "--aws-secret-key" and i + 1 < len(tokens):
                kwargs["aws_secret_key"] = tokens[i + 1]
                i += 2
            elif tokens[i] == "--aws-region" and i + 1 < len(tokens):
                kwargs["aws_region"] = tokens[i + 1]
                i += 2
            elif tokens[i] == "--models" and i + 1 < len(tokens):
                kwargs["models"] = [m.strip() for m in tokens[i + 1].split(",")]
                i += 2
            else:
                print(f"[unknown option: {tokens[i]}]")
                return
        try:
            result = await client.add_provider(name, ptype, **kwargs)
            models = result.get("models", [])
            print(f'[provider "{name}" added, {len(models)} model(s)]')
            for m in models:
                print(f"  - {m}")
        except Exception as exc:
            print(f"[error: {exc}]")

    elif sub == "remove":
        if len(tokens) < 3:
            print("[usage: /provider remove <name>]")
            return
        try:
            await client.remove_provider(tokens[2])
            print(f'[provider "{tokens[2]}" removed]')
        except Exception as exc:
            print(f"[error: {exc}]")

    elif sub == "refresh":
        if len(tokens) < 3:
            print("[usage: /provider refresh <name>]")
            return
        try:
            result = await client.refresh_models(tokens[2])
            models = result.get("models", [])
            print(f'[provider "{tokens[2]}" refreshed, {len(models)} model(s)]')
            for m in models:
                print(f"  - {m}")
        except Exception as exc:
            print(f"[error: {exc}]")

    else:
        print("[usage: /provider list|add|remove|refresh]")


async def _handle_model_command(client: ProbeClient, line: str) -> None:
    """Dispatch ``/model <subcommand> ...``."""
    try:
        tokens = shlex.split(line)
    except ValueError as exc:
        print(f"[parse error: {exc}]")
        return

    if len(tokens) < 2:
        print("[usage: /model current|use|list]")
        return

    sub = tokens[1]

    if sub == "use":
        if len(tokens) < 4:
            print("[usage: /model use [role] <provider> <model_id>]")
            return
        if len(tokens) >= 5:
            role = tokens[2]
            provider = tokens[3]
            model = tokens[4]
        else:
            role = "default"
            provider = tokens[2]
            model = tokens[3]
        try:
            result = await client.set_current_model(provider, model, role=role)
            ref = result.get("model", [])
            print(f"[current_used.{result.get('role', role)} set to {ref}]")
        except Exception as exc:
            print(f"[error: {exc}]")

    elif sub == "current":
        try:
            result = await client.list_providers()
            current = result.get("currentUsed", result.get("current_used", {}))
            if not current:
                print("[no current-used models configured]")
                return
            print("[current-used]")
            for role, ref in current.items():
                print(f"  {role}: {ref}")
        except Exception as exc:
            print(f"[error: {exc}]")

    elif sub == "list":
        try:
            result = await client.list_providers()
            providers = result.get("providers", [])
            if not providers:
                print("[no models available]")
                return
            print("[models]")
            for p in providers:
                name = p.get("name", "?")
                for m in p.get("models", []):
                    print(f"  [{name}, {m}]")
        except Exception as exc:
            print(f"[error: {exc}]")

    else:
        print("[usage: /model current|use|list]")


# ---------------------------------------------------------------------------
# Auth command
# ---------------------------------------------------------------------------


async def _handle_auth_command(client: ProbeClient, line: str) -> None:
    """Dispatch ``/auth <subcommand> ...``."""
    try:
        tokens = shlex.split(line)
    except ValueError as exc:
        print(f"[parse error: {exc}]")
        return

    if len(tokens) < 2:
        print("[usage: /auth set|get|list|delete|import-env]")
        return

    sub = tokens[1]

    if sub == "set":
        if len(tokens) < 4:
            print("[usage: /auth set <name> <value> [--kind static|bearer]]")
            return
        name, value = tokens[2], tokens[3]
        kind = "static"
        if "--kind" in tokens:
            idx = tokens.index("--kind")
            if idx + 1 < len(tokens):
                kind = tokens[idx + 1]
        try:
            await client._request("_mustang.agent/secrets/auth", {
                "action": "set", "name": name, "value": value, "kind": kind,
            })
            print(f'[secret "{name}" stored ({kind})]')
        except Exception as exc:
            print(f"[error: {exc}]")

    elif sub == "get":
        if len(tokens) < 3:
            print("[usage: /auth get <name>]")
            return
        try:
            result = await client._request("_mustang.agent/secrets/auth", {
                "action": "get", "name": tokens[2],
            })
            value = result.get("value")
            if value is None:
                print(f'[secret "{tokens[2]}" not found]')
            else:
                print(f"  {tokens[2]} = {value}")
        except Exception as exc:
            print(f"[error: {exc}]")

    elif sub == "list":
        kind = None
        if "--kind" in tokens:
            idx = tokens.index("--kind")
            if idx + 1 < len(tokens):
                kind = tokens[idx + 1]
        try:
            params: dict = {"action": "list"}
            if kind:
                params["kind"] = kind
            result = await client._request("_mustang.agent/secrets/auth", params)
            names = result.get("names", [])
            if not names:
                print("[no secrets stored]")
            else:
                print("[secrets]")
                for n in names:
                    print(f"  {n}")
        except Exception as exc:
            print(f"[error: {exc}]")

    elif sub == "delete":
        if len(tokens) < 3:
            print("[usage: /auth delete <name>]")
            return
        try:
            await client._request("_mustang.agent/secrets/auth", {
                "action": "delete", "name": tokens[2],
            })
            print(f'[secret "{tokens[2]}" deleted]')
        except Exception as exc:
            print(f"[error: {exc}]")

    elif sub == "import-env":
        if len(tokens) < 4:
            print("[usage: /auth import-env <ENV_VAR> <name>]")
            return
        try:
            await client._request("_mustang.agent/secrets/auth", {
                "action": "import_env", "env_var": tokens[2], "name": tokens[3],
            })
            print(f'[imported ${tokens[2]} as "{tokens[3]}"]')
        except Exception as exc:
            print(f"[error: {exc}]")

    else:
        print("[usage: /auth set|get|list|delete|import-env]")


# ---------------------------------------------------------------------------
# Event handling
# ---------------------------------------------------------------------------


async def _handle_event(
    event: "Event",
    client: ProbeClient,
    loop: asyncio.AbstractEventLoop,
    executor: ThreadPoolExecutor,
) -> None:
    """Print a single event from the prompt stream.

    Permission requests are answered inline by prompting the user.
    """
    if isinstance(event, AgentChunk):
        print(event.text, end="", flush=True)

    elif isinstance(event, UserChunk):
        # Only appears during session/load history replay (printed before REPL
        # loop starts), not during normal turns.
        print(f"[history:user] {event.text}")

    elif isinstance(event, ToolCallEvent):
        # New line so the tool tag doesn't run into streamed text.
        print(f"\n  [{event.kind}: {event.title}]", flush=True)

    elif isinstance(event, ToolCallUpdate):
        print(f"  [{event.tool_call_id}: {event.status}]", flush=True)

    elif isinstance(event, PermissionRequest):
        # AskUserQuestion: render questions and collect answers interactively.
        if event.tool_input is not None and isinstance(event.tool_input.get("questions"), list):
            await _handle_ask_user_question(event, client, loop, executor)
        else:
            options_str = ", ".join(f"{o['optionId']}={o['name']}" for o in event.options)
            label = event.tool_title or event.tool_call_id
            print(f"\n[permission for {label}]")
            if event.input_summary:
                print(f"  {event.input_summary}")
            # Show inner call details for REPL batches.
            _print_tool_input_details(event.tool_input)
            print(f"  options: {options_str}")
            choice = await loop.run_in_executor(executor, input, "  allow? > ")
            choice = choice.strip()
            # Default to first option if user just presses Enter.
            if not choice and event.options:
                choice = event.options[0]["optionId"]
            await client.reply_permission(event.req_id, choice)

    elif isinstance(event, TurnComplete):
        # Print a newline after the streamed text, then the stop reason.
        print(f"\n[{event.stop_reason}]")


def _print_tool_input_details(tool_input: dict[str, Any] | None) -> None:
    """Print human-readable details for inner tool calls (REPL batches)."""
    if not tool_input or not isinstance(tool_input.get("calls"), list):
        return
    for call in tool_input["calls"]:
        name = call.get("tool_name", "?")
        inp = call.get("input", {})
        if name == "Bash":
            cmd = inp.get("command", "")
            desc = inp.get("description", "")
            if desc:
                print(f"  > {name}: {desc}")
            if cmd:
                # Show full command (multi-line indented).
                for i, line in enumerate(cmd.splitlines()):
                    prefix = "  $ " if i == 0 else "    "
                    print(f"{prefix}{line}")
        elif name in ("Read", "FileRead"):
            print(f"  > {name}: {inp.get('file_path', '?')}")
        elif name in ("Edit", "FileEdit"):
            print(f"  > {name}: {inp.get('file_path', '?')}")
        elif name in ("Write", "FileWrite"):
            print(f"  > {name}: {inp.get('file_path', '?')}")
        elif name == "Glob":
            print(f"  > {name}: {inp.get('pattern', '?')}")
        elif name == "Grep":
            print(f"  > {name}: {inp.get('pattern', '?')}")
        else:
            print(f"  > {name}")


async def _handle_ask_user_question(
    event: PermissionRequest,
    client: ProbeClient,
    loop: asyncio.AbstractEventLoop,
    executor: ThreadPoolExecutor,
) -> None:
    """Render AskUserQuestion interactively and send answers back.

    Shows choice questions with numbered options and text questions
    with a direct answer prompt. Selecting "Other" asks for the custom
    text in a second prompt. Answers are returned via
    ``PermissionResponse.updated_input``.
    """
    questions = event.tool_input["questions"]  # type: ignore[index]
    answers: dict[str, str] = {}

    print("\n[AskUserQuestion]")
    for q in questions:
        q_text = q.get("question", "?")
        q_type = q.get("type", "choice")
        options = q.get("options", [])
        multi = q.get("multiSelect", False)

        print(f"\n  {q_text}")
        if q_type == "text":
            placeholder = q.get("placeholder")
            if placeholder:
                print(f"    hint: {placeholder}")
            raw = await loop.run_in_executor(executor, input, "  answer> ")
            answers[q_text] = raw.strip()
            continue

        for i, opt in enumerate(options, 1):
            label = opt.get("label", "")
            desc = opt.get("description", "")
            print(f"    {i}. {label} -- {desc}")
        print(f"    {len(options) + 1}. Other (type your answer)")

        hint = "  numbers (comma-sep)> " if multi else "  choice> "
        raw = await loop.run_in_executor(executor, input, hint)
        raw = raw.strip()

        if multi:
            # Parse comma-separated numbers.
            selected: list[str] = []
            for part in raw.split(","):
                part = part.strip()
                try:
                    idx = int(part)
                    if 1 <= idx <= len(options):
                        selected.append(options[idx - 1]["label"])
                    elif idx == len(options) + 1:
                        other = await loop.run_in_executor(executor, input, "  other> ")
                        if other.strip():
                            selected.append(other.strip())
                    else:
                        selected.append(part)
                except ValueError:
                    selected.append(part)
            answers[q_text] = ", ".join(selected) if selected else raw
        else:
            try:
                idx = int(raw)
                if 1 <= idx <= len(options):
                    answers[q_text] = options[idx - 1]["label"]
                elif idx == len(options) + 1:
                    other = await loop.run_in_executor(executor, input, "  other> ")
                    answers[q_text] = other.strip()
                else:
                    answers[q_text] = raw
            except ValueError:
                answers[q_text] = raw

    print()
    await client.reply_permission(
        event.req_id,
        "allow_once",
        updated_input={
            "questions": questions,
            "answers": answers,
        },
    )
