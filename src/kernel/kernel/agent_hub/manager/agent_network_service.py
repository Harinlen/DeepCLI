"""Agent-visible network service boundary.

This service is the authority-side facade for agent-to-agent discovery and
messaging.  Mustang runtime tools call into this boundary through
``ToolContext``; they must not write ResourceStore directly or import
AccessRouter themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kernel.agent_hub.manager.command_surface import AgentCommandService
from kernel.agent_hub.manager.runtime_backends import AcpRuntimeController
from kernel.agent_hub.manager.spawned_runs import SpawnedRunRegistry


@dataclass(frozen=True)
class AgentNetworkPolicy:
    """Minimal allowlist policy for the first Agent Network slice."""

    allow_agents: frozenset[str] = field(default_factory=frozenset)

    def allows(self, agent_id: str) -> bool:
        return not self.allow_agents or agent_id in self.allow_agents or _display_agent_id(agent_id) in self.allow_agents


class AgentNetworkService:
    """Policy-filtered Agent Network operations exposed to agent tools."""

    def __init__(
        self,
        command_service: AgentCommandService,
        *,
        policy: AgentNetworkPolicy | None = None,
        run_registry: SpawnedRunRegistry | None = None,
        acp_controller: AcpRuntimeController | None = None,
    ) -> None:
        self._commands = command_service
        self._policy = policy or AgentNetworkPolicy()
        self._runs = run_registry
        self._acp = acp_controller

    async def request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one Agent Network action from a runtime tool."""
        if action == "directory":
            return self.list_visible_agents()
        if action == "message":
            return await self.send_message(
                agent_id=str(payload.get("agentId") or payload.get("agent_id") or ""),
                message=str(payload.get("message") or ""),
                session_id=_optional_str(payload.get("sessionId") or payload.get("session_id")),
                run_id=_optional_str(payload.get("runId") or payload.get("run_id")),
                wait=bool(payload.get("wait", False)),
                timeout_seconds=_optional_int(
                    payload.get("timeoutSeconds") or payload.get("timeout_seconds")
                ),
                announce=bool(payload.get("announce", False)),
                reply_back=bool(payload.get("replyBack", payload.get("reply_back", False))),
            )
        if action == "session":
            return await self.session_request(payload)
        raise ValueError(f"unsupported Agent Network action: {action}")

    def list_visible_agents(self) -> dict[str, Any]:
        """Return agents visible to the caller after policy filtering."""
        rows = self._commands.list(include_bindings=True)
        agents: list[dict[str, Any]] = []
        for raw in rows.get("agents", []):
            internal_id = str(raw.get("agent_id") or raw.get("agentId") or raw.get("id") or "")
            agent_id = _display_agent_id(internal_id)
            if not agent_id or not self._policy.allows(agent_id):
                continue
            agents.append(
                {
                    "agentId": agent_id,
                    "legacyAgentId": internal_id if internal_id != agent_id else None,
                    "name": raw.get("name") or agent_id,
                    "description": raw.get("description") or raw.get("role") or "",
                    "role": raw.get("role") or "",
                    "runtimeKind": _runtime_kind(raw),
                    "workspaceHint": raw.get("workspace"),
                    "status": raw.get("status") or "unknown",
                    "canSend": True,
                    "canSpawn": False,
                    "allowedModes": ["message"],
                    "labels": raw.get("labels") or [],
                    "whenToUse": raw.get("when_to_use") or raw.get("whenToUse") or "",
                    "examples": raw.get("examples") or [],
                }
            )
        return {"available": True, "agents": agents}

    async def send_message(
        self,
        *,
        agent_id: str,
        message: str,
        session_id: str | None = None,
        run_id: str | None = None,
        requester_agent_id: str = "primary",
        wait: bool = False,
        timeout_seconds: int | None = None,
        announce: bool = False,
        reply_back: bool = False,
    ) -> dict[str, Any]:
        """Send a message through the proven Access Router delivery path."""
        if run_id is not None:
            if self._runs is None:
                return {"success": False, "runId": run_id, "error": "run_registry_unavailable"}
            run = self._runs.get_owned(run_id, requester_agent_id=requester_agent_id)
            if run is None:
                return {"success": False, "runId": run_id, "error": "run_not_found"}
            self._runs.update_message(run_id, requester_agent_id=requester_agent_id, message=message)
            agent_id = run.target_agent_id
            session_id = run.session_id
        if not agent_id:
            return {"success": False, "error": "agent_id_required"}
        agent_id = _internal_agent_id(agent_id)
        if not message:
            return {"success": False, "agentId": agent_id, "error": "message_required"}
        if not self._policy.allows(agent_id):
            return {"success": False, "agentId": agent_id, "denied": True, "error": "policy_denied"}
        try:
            delivered = await self._commands.send(
                agent_id=agent_id,
                message=message,
                session_id=session_id,
            )
        except RuntimeError as exc:
            if run_id is not None and self._runs is not None:
                self._runs.fail(run_id, requester_agent_id=requester_agent_id, error=str(exc))
            return {
                "success": False,
                "agentId": agent_id,
                "route": "access_router",
                "error": "route_unavailable",
                "detail": str(exc),
                "accepted": False,
                "waited": False,
                "timedOut": False,
            }
        if run_id is not None and self._runs is not None:
            self._runs.complete(run_id, requester_agent_id=requester_agent_id, result=delivered)
        provenance = {
            "kind": "inter_session",
            "requesterAgentId": requester_agent_id,
            "targetAgentId": agent_id,
            "targetSessionId": session_id,
            "runId": run_id,
        }
        return {
            "success": bool(delivered.get("delivered", delivered)),
            "agentId": agent_id,
            "route": "access_router",
            "delivered": delivered,
            "accepted": True,
            "waited": wait,
            "timedOut": False,
            "timeoutSeconds": timeout_seconds,
            "replyBackEnabled": reply_back,
            "announce": (
                {
                    "kind": "completion",
                    "text": f"Agent {agent_id} accepted the message.",
                }
                if announce
                else None
            ),
            "provenance": provenance,
        }

    async def session_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Manage caller-owned spawned Agent runs."""
        runtime = str(payload.get("runtime") or "agent")
        action = str(payload.get("action") or "spawn")
        requester_agent_id = str(payload.get("requesterAgentId") or "primary")
        parent_session_id = str(payload.get("parentSessionId") or "unknown")
        if runtime == "acp":
            return await self._acp_session_request(payload, requester_agent_id, parent_session_id)
        if runtime == "agent":
            if self._runs is None:
                return {"success": False, "runtime": runtime, "error": "run_registry_unavailable"}
            if action == "spawn":
                target = str(payload.get("agentId") or payload.get("agent_id") or "main")
                task = str(payload.get("task") or payload.get("message") or "")
                mode = str(payload.get("mode") or "run")
                timeout_seconds = _optional_int(
                    payload.get("timeoutSeconds") or payload.get("timeout_seconds")
                )
                wait_mode = "wait" if payload.get("wait") else "accepted"
                reply_back = bool(payload.get("replyBack", payload.get("reply_back", False)))
                announce = bool(payload.get("announce", False))
                binding_id = _optional_str(payload.get("bindingId") or payload.get("binding_id"))
                if not self._policy.allows(target):
                    return {"success": False, "agentId": target, "denied": True, "error": "policy_denied"}
                target = _internal_agent_id(target)
                run = self._runs.spawn(
                    parent_session_id=parent_session_id,
                    requester_agent_id=requester_agent_id,
                    target_agent_id=target,
                    runtime=runtime,
                    mode=mode,
                    task=task,
                    binding_id=binding_id,
                    timeout_seconds=timeout_seconds,
                    wait_mode=wait_mode,
                    reply_back_enabled=reply_back,
                    announce_enabled=announce,
                )
                delivery: dict[str, Any] | None = None
                if task:
                    delivery = await self.send_message(
                        agent_id=target,
                        session_id=run.session_id,
                        message=_subagent_prompt(task, requester_agent_id=requester_agent_id),
                        run_id=run.run_id,
                        requester_agent_id=requester_agent_id,
                        wait=bool(payload.get("wait", mode == "run")),
                        timeout_seconds=timeout_seconds,
                        announce=announce,
                        reply_back=reply_back,
                    )
                refreshed = self._runs.get_owned(run.run_id, requester_agent_id=requester_agent_id) or run
                return {
                    "success": True,
                    "run": refreshed.to_dict(),
                    "runId": run.run_id,
                    "sessionId": run.session_id,
                    "accepted": True,
                    "delivery": delivery,
                    "announce": (
                        {"kind": "spawned", "text": f"Spawned {target} run {run.run_id}."}
                        if announce
                        else None
                    ),
                }
            if action == "list":
                return {
                    "success": True,
                    "runs": [run.to_dict() for run in self._runs.list(requester_agent_id=requester_agent_id)],
                }
            run_id = str(payload.get("runId") or payload.get("run_id") or "")
            if not run_id:
                return {"success": False, "error": "run_id_required"}
            if action == "status":
                run = self._runs.get_owned(run_id, requester_agent_id=requester_agent_id)
                return {
                    "success": run is not None,
                    "run": run.to_dict() if run else None,
                    "events": self._runs.events(run_id, requester_agent_id=requester_agent_id)
                    if run
                    else [],
                }
            if action == "stop":
                return {"success": True, "run": self._runs.stop(run_id, requester_agent_id=requester_agent_id).to_dict()}
            if action == "steer":
                message = str(payload.get("message") or "")
                return {
                    "success": True,
                    "run": self._runs.steer(
                        run_id,
                        requester_agent_id=requester_agent_id,
                        message=message,
                    ).to_dict(),
                }
            return {"success": False, "error": "unsupported_action", "action": action}
        if runtime == "local":
            return {
                "success": False,
                "runtime": runtime,
                "error": "local_compatibility_requires_agent_tool",
                "compatibility": True,
            }
        return {"success": False, "runtime": runtime, "error": "unsupported_runtime"}

    async def _acp_session_request(
        self,
        payload: dict[str, Any],
        requester_agent_id: str,
        parent_session_id: str,
    ) -> dict[str, Any]:
        if self._acp is None:
            return {
                "success": False,
                "runtime": "acp",
                "error": "acp_runtime_unavailable",
                "unsupported": True,
            }
        action = str(payload.get("action") or "spawn")
        cwd = str(payload.get("cwd") or payload.get("workspace") or ".")
        if action == "spawn":
            task = str(payload.get("task") or payload.get("message") or "")
            created = await self._acp.new(cwd=cwd)
            acp_session_id = str(created["sessionId"])
            run = None
            if self._runs is not None:
                run = self._runs.spawn(
                    parent_session_id=parent_session_id,
                    requester_agent_id=requester_agent_id,
                    target_agent_id=str(payload.get("agentId") or "acp"),
                    runtime="acp",
                    mode=str(payload.get("mode") or "run"),
                    task=task,
                    acp_session_id=acp_session_id,
                    wait_mode="wait" if payload.get("wait") else "accepted",
                    timeout_seconds=_optional_int(
                        payload.get("timeoutSeconds") or payload.get("timeout_seconds")
                    ),
                    reply_back_enabled=bool(payload.get("replyBack", False)),
                    announce_enabled=bool(payload.get("announce", False)),
                )
            prompted = await self._acp.prompt(session_id=acp_session_id, text=task) if task else None
            if run is not None and prompted is not None:
                if prompted.get("success"):
                    self._runs.complete(
                        run.run_id,
                        requester_agent_id=requester_agent_id,
                        result=prompted,
                    )
                else:
                    self._runs.fail(
                        run.run_id,
                        requester_agent_id=requester_agent_id,
                        error=str(prompted.get("error") or "acp_prompt_failed"),
                    )
            return {
                "success": bool(prompted.get("success", True)) if prompted else True,
                "runtime": "acp",
                "sessionId": acp_session_id,
                "runId": run.run_id if run else None,
                "run": (
                    self._runs.get_owned(run.run_id, requester_agent_id=requester_agent_id).to_dict()
                    if run and self._runs
                    else None
                ),
                "prompt": prompted,
            }
        session_id = str(payload.get("sessionId") or payload.get("acpSessionId") or "")
        if not session_id:
            return {"success": False, "runtime": "acp", "error": "session_id_required"}
        if action == "status":
            return {"success": True, "runtime": "acp", "status": self._acp.status()}
        if action == "stop":
            return await self._acp.cancel(session_id=session_id)
        if action == "steer":
            message = str(payload.get("message") or "")
            return await self._acp.prompt(session_id=session_id, text=message)
        if action == "close":
            return await self._acp.close_session(session_id=session_id)
        return {"success": False, "runtime": "acp", "error": "unsupported_action", "action": action}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _runtime_kind(raw: dict[str, Any]) -> str:
    runtime = raw.get("runtime")
    if isinstance(runtime, dict):
        return str(runtime.get("kind") or "agent")
    return str(runtime or "agent")


def _display_agent_id(agent_id: str) -> str:
    return "main" if agent_id == "primary" else agent_id


def _internal_agent_id(agent_id: str) -> str:
    return "primary" if agent_id == "main" else agent_id


def _subagent_prompt(task: str, *, requester_agent_id: str) -> str:
    return (
        "# Subagent Context\n"
        f"You are a subagent spawned by {requester_agent_id}. Focus only on the task. "
        "Your final result is automatically routed back to the requester; do not run "
        "heartbeat or side tasks.\n\n"
        f"# Task\n{task}"
    )
