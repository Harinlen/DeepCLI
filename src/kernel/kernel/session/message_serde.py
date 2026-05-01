"""Serialize / deserialize ``Message`` dataclasses for session persistence.

The ``ConversationMessageEvent`` and ``ConversationSnapshotEvent`` store
Message objects as plain dicts (``dataclasses.asdict`` output).  This
module handles the round-trip conversion.

No kernel imports beyond ``kernel.llm.types`` — keeps the dependency
footprint minimal.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from kernel.llm.types import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
    UserMessage,
)

logger = logging.getLogger(__name__)


def serialize_message(msg: Message) -> dict[str, Any]:
    """Convert a ``Message`` dataclass to a JSON-serializable dict."""
    return dataclasses.asdict(msg)


def deserialize_message(data: dict[str, Any]) -> Message:
    """Reconstruct a ``Message`` from a dict.

    Raises:
        ValueError: ``data["role"]`` is neither ``"user"`` nor ``"assistant"``.
    """
    role = data.get("role")
    if role == "user":
        user_content = [_deserialize_user_content(b) for b in data["content"]]
        return UserMessage(content=user_content)
    if role == "assistant":
        asst_content = [_deserialize_assistant_content(b) for b in data["content"]]
        return AssistantMessage(content=asst_content)
    raise ValueError(f"Unknown message role: {role!r}")


_UserContent = TextContent | ImageContent | ToolResultContent
_AssistantContent = TextContent | ToolUseContent | ThinkingContent


def _deserialize_tool_result_block(b: dict[str, Any]) -> TextContent | ImageContent:
    """Tool result blocks may only contain text or image — never a nested tool_result."""
    kind = b.get("type")
    if kind == "text":
        return TextContent(text=b["text"])
    if kind == "image":
        return ImageContent(media_type=b["media_type"], data_base64=b["data_base64"])
    logger.warning("Unknown tool_result block type %r — treating as text", kind)
    return TextContent(text=str(b))


def _deserialize_user_content(b: dict[str, Any]) -> _UserContent:
    match b.get("type"):
        case "text":
            return TextContent(text=b["text"])
        case "image":
            return ImageContent(
                media_type=b["media_type"],
                data_base64=b["data_base64"],
            )
        case "tool_result":
            raw = b["content"]
            content: str | list[TextContent | ImageContent]
            if isinstance(raw, str):
                content = raw
            else:
                content = [_deserialize_tool_result_block(x) for x in raw]
            return ToolResultContent(
                tool_use_id=b["tool_use_id"],
                content=content,
                is_error=b.get("is_error", False),
            )
        case _:
            logger.warning(
                "Unknown user content type %r — treating as text",
                b.get("type"),
            )
            return TextContent(text=str(b))


def _deserialize_assistant_content(b: dict[str, Any]) -> _AssistantContent:
    match b.get("type"):
        case "text":
            return TextContent(text=b["text"])
        case "tool_use":
            return ToolUseContent(
                id=b["id"],
                name=b["name"],
                input=b["input"],
            )
        case "thinking":
            return ThinkingContent(
                thinking=b["thinking"],
                signature=b["signature"],
            )
        case _:
            logger.warning(
                "Unknown assistant content type %r — treating as text",
                b.get("type"),
            )
            return TextContent(text=str(b))
