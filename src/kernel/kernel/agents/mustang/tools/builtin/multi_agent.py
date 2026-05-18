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


class AgentsListTool(Tool[dict[str, Any], dict[str, Any]]):
    """List durable Agents known to Agent Hub.Manager."""

    name = "agents_list"
    description = "List durable Agents registered in Agent Hub."
    kind = ToolKind.read
    input_schema = {"type": "object", "properties": {}}

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        hub = _agent_hub_from_context(ctx)
        if hub is None:
            yield _result(
                {"agents": [], "available": False},
                "Agent Hub is not available in this session.",
            )
            return

        agents = [
            {
                "id": definition.id,
                "name": definition.name,
                "role": definition.role.value,
                "runtime": definition.runtime.kind.value,
                "workspace": definition.workspace,
                "nativeDefault": definition.bindings.native_default,
                "platformBindings": len(definition.bindings.platforms),
            }
            for definition in hub.manager.list_definitions()
        ]
        if not agents:
            yield _result({"agents": [], "available": True}, "No durable Agents are defined.")
            return

        lines = ["Durable Agents:"]
        for agent in agents:
            default = " (native default)" if agent["nativeDefault"] else ""
            lines.append(f"- {agent['id']}: {agent['name']} [{agent['runtime']}]{default}")
        yield _result({"agents": agents, "available": True}, "\n".join(lines))


class SessionsSendTool(Tool[dict[str, Any], dict[str, Any]]):
    """Send a message to a durable Agent or an existing session."""

    name = "sessions_send"
    description = "Send a message to a durable Agent or an existing session."
    kind = ToolKind.execute
    is_concurrency_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "target_agent_id": {"type": "string"},
            "target_session_id": {"type": "string"},
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
        target_agent_id = input.get("target_agent_id")
        target_session_id = input.get("target_session_id")

        if bool(target_agent_id) == bool(target_session_id):
            yield _result(
                {"success": False},
                "Provide exactly one of target_agent_id or target_session_id.",
            )
            return

        if target_agent_id:
            if ctx.route_agent_message is None:
                yield _result(
                    {"success": False, "target_agent_id": target_agent_id},
                    "Durable-agent routing is not available.",
                )
                return
            success = ctx.route_agent_message(str(target_agent_id), message)
            text = (
                f"Message routed to agent {target_agent_id}."
                if success
                else f"Agent {target_agent_id} was not routable."
            )
            yield _result(
                {"success": success, "target_agent_id": target_agent_id},
                text,
            )
            return

        if ctx.deliver_cross_session is None:
            yield _result(
                {"success": False, "target_session_id": target_session_id},
                "Cross-session delivery is not available.",
            )
            return
        success = ctx.deliver_cross_session(str(target_session_id), message)
        text = (
            f"Message delivered to session {target_session_id}."
            if success
            else f"Session {target_session_id} is not active."
        )
        yield _result({"success": success, "target_session_id": target_session_id}, text)


class SessionsSpawnTool(Tool[dict[str, Any], dict[str, Any]]):
    """Spawn an in-session Agent task using the OpenClaw session verb."""

    name = "sessions_spawn"
    description = "Spawn a background Agent session for a task."
    kind = ToolKind.orchestrate
    input_schema = {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "prompt": {"type": "string"},
            "name": {"type": "string"},
            "subagent_type": {"type": "string"},
            "model": {"type": "string"},
            "background": {"type": "boolean"},
        },
        "required": ["description", "prompt"],
    }

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        from kernel.agents.mustang.tools.builtin.agent import AgentTool

        agent_input = {
            "description": input["description"],
            "prompt": input["prompt"],
            "run_in_background": input.get("background", True),
        }
        for key in ("name", "subagent_type", "model"):
            if key in input:
                agent_input[key] = input[key]

        async for event in AgentTool().call(agent_input, ctx):
            yield event


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


def _agent_hub_from_context(ctx: ToolContext) -> Any | None:
    module_table = ctx.module_table
    if module_table is None:
        return None
    return getattr(module_table, "agent_hub", None)


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
