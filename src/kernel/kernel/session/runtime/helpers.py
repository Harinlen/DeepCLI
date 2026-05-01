"""Small stateless helpers used by the session subsystem."""

from __future__ import annotations

import base64
import logging
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from kernel.orchestrator.types import StopReason as OrchestratorStopReason
from kernel.session.runtime.config_options import config_descriptor_dicts

logger = logging.getLogger(__name__)


def get_git_branch(cwd: Path) -> str | None:
    """Return the current git branch for ``cwd``, or ``None`` on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            return branch if branch and branch != "HEAD" else None
    except Exception:
        logger.debug("git branch detection failed for cwd=%s", cwd)
    return None


def map_orch_stop_reason(
    reason: OrchestratorStopReason,
) -> Literal["end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"]:
    """Map orchestrator stop reasons to ACP prompt result values."""
    mapping: dict[
        OrchestratorStopReason,
        Literal["end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"],
    ] = {
        OrchestratorStopReason.end_turn: "end_turn",
        OrchestratorStopReason.max_turns: "max_turn_requests",
        OrchestratorStopReason.cancelled: "cancelled",
        OrchestratorStopReason.error: "end_turn",
    }
    if reason == OrchestratorStopReason.error:
        logger.warning("Orchestrator returned StopReason.error — treating as end_turn")
    return mapping.get(reason, "end_turn")


def encode_cursor(modified: str, session_id: str) -> str:
    """Encode a stable list pagination cursor."""
    raw = f"{modified}|{session_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(token: str) -> tuple[str, str]:
    """Decode a cursor produced by :func:`encode_cursor`."""
    raw = base64.urlsafe_b64decode(token.encode()).decode()
    modified, session_id = raw.split("|", 1)
    return modified, session_id


def config_list(options: dict[str, Any]) -> list[dict[str, Any]]:
    """Return ACP config option list shape from a full-state mapping."""
    return config_descriptor_dicts(options)


def make_summarise_closure(llm_manager: Any) -> Callable[[str, str], Awaitable[str]] | None:
    """Build the summarise closure passed into orchestrator dependencies."""
    if llm_manager is None:
        return None

    async def summarise(content: str, _user_prompt: str) -> str:
        from kernel.llm.types import PromptSection, TextChunk, TextContent, UserMessage

        model_ref = llm_manager.model_for_or_default("compact")
        messages = [UserMessage(content=[TextContent(text=content)])]
        system = [
            PromptSection(
                text="You are a concise summariser. Follow the user's instructions exactly.",
                cache=False,
            )
        ]

        stream = await llm_manager.stream(
            system=system,
            messages=messages,
            tool_schemas=[],
            model=model_ref,
            temperature=None,
            thinking=False,
            max_tokens=None,
        )

        collected: list[str] = []
        async for chunk in stream:
            if isinstance(chunk, TextChunk):
                collected.append(chunk.content)
                continue
            text = getattr(chunk, "text", None)
            if isinstance(text, str):
                collected.append(text)
                continue
            delta = getattr(chunk, "delta", None)
            if isinstance(delta, str):
                collected.append(delta)
        return "".join(collected)

    return summarise
