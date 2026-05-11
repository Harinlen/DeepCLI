"""RestartSelf — request a supervised restart of the current Agent Runtime."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from kernel.agents.mustang.orchestrator.types import ToolKind
from kernel.core.protocol.interfaces.contracts.text_block import TextBlock
from kernel.agents.mustang.tools.context import ToolContext
from kernel.agents.mustang.tools.tool import RiskContext, Tool
from kernel.agents.mustang.tools.types import PermissionSuggestion, TextDisplay, ToolCallProgress, ToolCallResult


class RestartSelfTool(Tool[dict[str, Any], dict[str, Any]]):
    """Schedule a self-restart through Supervisor after this turn is flushed."""

    name = "RestartSelf"
    description_key = "tools/restart_self"
    description = "Restart this agent runtime through Supervisor after the current turn is saved."
    kind = ToolKind.other
    interrupt_behavior = "block"

    input_schema = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Short reason for restarting this agent runtime.",
            }
        },
    }

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="medium",
            default_decision="ask",
            reason="agent runtime self-restart",
        )

    def is_read_only_call(self, input: dict[str, Any], ctx: RiskContext) -> bool:
        return False

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        reason = str(input.get("reason") or "agent requested self-restart")
        agent_id = _agent_id(ctx)
        text = "Self-restart scheduled. This agent runtime will restart after this turn is saved."
        yield ToolCallResult(
            data={"agent_id": agent_id, "reason": reason},
            llm_content=[TextBlock(type="text", text=text)],
            display=TextDisplay(text=text),
            meta={
                "mustang.agent/restartSelf": {
                    "agentId": agent_id,
                    "reason": reason,
                    "afterAck": True,
                }
            },
        )


def _agent_id(ctx: ToolContext) -> str:
    if ctx.agent_id:
        return ctx.agent_id
    return "primary"


__all__ = ["RestartSelfTool"]
