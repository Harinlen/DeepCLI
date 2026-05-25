"""OpenClaw-aligned multi-agent tools.

These tools expose the vocabulary used by OpenClaw's multi-agent runtime:
``agents_list`` for durable Agent discovery, ``sessions_spawn`` for creating
agent work, ``sessions_send`` for message delivery, and ``subagents`` for the
legacy in-session background Agent view.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from kernel.agents.mustang.orchestrator.types import ToolKind
from kernel.agents.mustang.tasks.types import AgentTaskState, TaskStatus
from kernel.agents.mustang.tools.context import ToolContext
from kernel.agents.mustang.tools.tool import Tool
from kernel.agents.mustang.tools.types import TextDisplay, ToolCallProgress, ToolCallResult
from kernel.core.protocol.interfaces.contracts.text_block import TextBlock


class AgentDirectoryTool(Tool[dict[str, Any], dict[str, Any]]):
    """Discover durable Agents visible to the current session."""

    name = "AgentDirectory"
    aliases = ("agents_list",)
    description = "List durable Agents this session is allowed to contact or spawn."
    kind = ToolKind.read
    input_schema = {"type": "object", "properties": {}}

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        if ctx.agent_network_request is None:
            yield _result(
                {"agents": [], "available": False},
                "Agent Network is not available in this session.",
            )
            return

        result = await ctx.agent_network_request("directory", {})
        agents = list(result.get("agents") or [])
        if not agents:
            yield _result({"agents": [], "available": result.get("available", True)}, "No visible Agents.")
            return

        lines = ["Durable Agents:"]
        for agent in agents:
            flags = []
            if agent.get("canSend"):
                flags.append("send")
            if agent.get("canSpawn"):
                flags.append("spawn")
            suffix = f" ({', '.join(flags)})" if flags else ""
            lines.append(
                f"- {agent.get('agentId')}: {agent.get('name')} "
                f"[{agent.get('runtimeKind', 'agent')}]{suffix}"
            )
        yield _result({"agents": agents, "available": result.get("available", True)}, "\n".join(lines))


class AgentMessageTool(Tool[dict[str, Any], dict[str, Any]]):
    """Send a message to a durable Agent or an existing session."""

    name = "AgentMessage"
    aliases = ("sessions_send",)
    description = "Send a message to a durable Agent or an existing session."
    kind = ToolKind.execute
    is_concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "agentId": {"type": "string"},
            "sessionId": {"type": "string"},
            "runId": {"type": "string"},
            "target_agent_id": {"type": "string", "deprecated": True},
            "target_session_id": {"type": "string", "deprecated": True},
            "wait": {"type": "boolean", "default": False},
            "timeoutSeconds": {"type": "integer"},
            "announce": {"type": "boolean", "default": False},
            "replyBack": {"type": "boolean", "default": False},
        },
        "required": ["message"],
    }

    def is_read_only_call(self, input: dict[str, Any], ctx: Any) -> bool:
        return isinstance(input.get("message"), str)

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        message = str(input["message"])
        target_agent_id = input.get("agentId") or input.get("target_agent_id")
        target_session_id = input.get("sessionId") or input.get("target_session_id")
        target_run_id = input.get("runId")
        options = {
            "wait": bool(input.get("wait", False)),
            "timeoutSeconds": input.get("timeoutSeconds"),
            "announce": bool(input.get("announce", False)),
            "replyBack": bool(input.get("replyBack", False)),
        }

        target_count = sum(bool(value) for value in (target_agent_id, target_session_id, target_run_id))
        if target_count != 1:
            yield _result(
                {"success": False},
                "Provide exactly one of agentId, sessionId, or runId.",
            )
            return

        if ctx.agent_network_request is None:
            yield _result({"success": False}, "Agent Network is not available.")
            return

        if target_agent_id:
            result = await ctx.agent_network_request(
                "message",
                {"agentId": str(target_agent_id), "message": message, **options},
            )
            success = bool(result.get("success"))
            text = f"Message routed to agent {target_agent_id}." if success else _error_text(result)
            yield _result(
                {**result, "success": success, "agentId": str(target_agent_id)},
                text,
            )
            return

        result = await ctx.agent_network_request(
            "message",
            {
                "sessionId": str(target_session_id) if target_session_id else None,
                "runId": str(target_run_id) if target_run_id else None,
                "message": message,
                **options,
            },
        )
        success = bool(result.get("success"))
        target = target_session_id or target_run_id
        text = (
            f"Message delivered to {target}."
            if success
            else _error_text(result)
        )
        yield _result({**result, "success": success}, text)


class AgentSessionTool(Tool[dict[str, Any], dict[str, Any]]):
    """Create or control durable Agent Network sessions."""

    name = "AgentSession"
    aliases = ("sessions_spawn", "subagents")
    description = "Spawn, list, stop, or steer durable Agent Network sessions."
    kind = ToolKind.orchestrate
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["spawn", "list", "status", "stop", "steer", "close"],
                "default": "spawn",
            },
            "runtime": {"type": "string", "enum": ["agent", "acp", "local"], "default": "agent"},
            "agentId": {"type": "string"},
            "task": {"type": "string"},
            "mode": {"type": "string", "enum": ["run", "session"], "default": "run"},
            "runId": {"type": "string"},
            "sessionId": {"type": "string"},
            "cwd": {"type": "string"},
            "message": {"type": "string"},
            "wait": {"type": "boolean", "default": False},
            "timeoutSeconds": {"type": "integer"},
            "announce": {"type": "boolean", "default": False},
            "replyBack": {"type": "boolean", "default": False},
            "bindingId": {"type": "string"},
        },
    }

    def is_read_only_call(self, input: dict[str, Any], ctx: Any) -> bool:
        return input.get("action", "spawn") in {"list", "status"}

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        runtime = str(input.get("runtime") or "agent")
        if runtime == "local":
            result = {
                "success": False,
                "runtime": "local",
                "compatibility": True,
                "error": "local_compatibility_requires_agent_tool",
            }
            yield _result(
                result,
                "Local compatibility is provided by the Agent tool, not durable AgentSession.",
            )
            return
        if ctx.agent_network_request is None:
            yield _result({"success": False, "error": "agent_network_unavailable"}, "Agent Network is not available.")
            return
        result = await ctx.agent_network_request("session", dict(input))
        if result.get("unsupported"):
            text = "AgentSession is not implemented for this runtime yet."
        elif result.get("success") and result.get("runId"):
            text = f"AgentSession spawned run {result['runId']}."
        else:
            text = str(result)
        yield _result(result, text)


class AgentsListTool(AgentDirectoryTool):
    """Deprecated compatibility wrapper for ``AgentDirectory``."""

    name = "agents_list"
    aliases = ()


class SessionsSendTool(AgentMessageTool):
    """Deprecated compatibility wrapper for ``AgentMessage``."""

    name = "sessions_send"
    aliases = ()


class SessionsSpawnTool(AgentSessionTool):
    """Deprecated compatibility wrapper for ``AgentSession(action='spawn')``."""

    name = "sessions_spawn"
    aliases = ()


class SubagentsTool(Tool[dict[str, Any], dict[str, Any]]):
    """Inspect or send to in-session background Agent tasks."""

    name = "subagents"
    description = "List, inspect, stop, or message in-session background Agents."
    kind = ToolKind.execute
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "info", "send", "kill"],
                "default": "list",
            },
            "agent": {"type": "string"},
            "message": {"type": "string"},
        },
    }

    def is_read_only_call(self, input: dict[str, Any], ctx: Any) -> bool:
        return input.get("action", "list") in {"list", "info"}

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        registry = ctx.tasks
        if registry is None:
            yield _result({"success": False}, "Task registry is not available.")
            return

        action = input.get("action", "list")
        if action == "list":
            agents = [
                _agent_task_dict(task)
                for task in registry.get_all()
                if isinstance(task, AgentTaskState)
            ]
            text = "No in-session subagents." if not agents else _format_agent_tasks(agents)
            yield _result({"agents": agents}, text)
            return

        agent_ref = input.get("agent")
        if not agent_ref:
            yield _result({"success": False}, "agent is required for this action.")
            return
        task = _resolve_agent_task(registry, str(agent_ref))
        if task is None:
            yield _result({"success": False}, f"Subagent {agent_ref} was not found.")
            return

        if action == "info":
            data = _agent_task_dict(task)
            yield _result(data, _format_agent_tasks([data]))
            return

        if action == "send":
            message = input.get("message")
            if not isinstance(message, str) or not message:
                yield _result({"success": False}, "message is required for send.")
                return
            if task.status != TaskStatus.running:
                yield _result(
                    {"success": False, "agent": task.id},
                    f"Subagent {agent_ref} is {task.status.value}, not running.",
                )
                return
            registry.queue_message(task.id, message)
            yield _result({"success": True, "agent": task.id}, f"Message queued for {agent_ref}.")
            return

        if action == "kill":
            registry.update_status(task.id, TaskStatus.killed)
            yield _result({"success": True, "agent": task.id}, f"Subagent {agent_ref} killed.")
            return

        yield _result({"success": False}, f"Unknown subagents action: {action}")


def _resolve_agent_task(registry: Any, ref: str) -> AgentTaskState | None:
    task_id = registry.resolve_name(ref) or ref
    task = registry.get(task_id)
    if isinstance(task, AgentTaskState):
        return task
    return None


def _agent_task_dict(task: AgentTaskState) -> dict[str, Any]:
    return {
        "id": task.id,
        "name": task.name,
        "status": task.status.value,
        "description": task.description,
        "agentType": task.agent_type,
        "model": task.model,
        "pendingMessages": len(task.pending_messages),
    }


def _format_agent_tasks(agents: list[dict[str, Any]]) -> str:
    lines = ["Subagents:"]
    for agent in agents:
        name = f" ({agent['name']})" if agent.get("name") else ""
        lines.append(f"- {agent['id']}{name}: {agent['status']} - {agent['description']}")
    return "\n".join(lines)


def _result(data: dict[str, Any], text: str) -> ToolCallResult:
    return ToolCallResult(
        data=data,
        llm_content=[TextBlock(type="text", text=text)],
        display=TextDisplay(text=text),
    )


def _error_text(result: dict[str, Any]) -> str:
    error = result.get("error")
    if error:
        return str(error).replace("_", " ")
    if result.get("denied"):
        return "Agent Network policy denied the request."
    return "Agent Network request failed."
