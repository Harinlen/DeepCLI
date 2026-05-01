"""Convert universal types → Anthropic Messages API format.

All functions are pure — no SDK imports, no I/O.
"""

from __future__ import annotations

from typing import Any

from kernel.llm.types import (
    AssistantMessage,
    ImageContent,
    Message,
    PromptSection,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolSchema,
    ToolUseContent,
    UserMessage,
)

_CACHE_CONTROL = {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def sections_to_anthropic(
    sections: list[PromptSection],
    *,
    prompt_caching: bool,
) -> list[dict[str, Any]]:
    """Convert ``PromptSection`` list → Anthropic ``system`` parameter."""
    result: list[dict[str, Any]] = []
    for section in sections:
        block: dict[str, Any] = {"type": "text", "text": section.text}
        if prompt_caching and section.cache:
            block["cache_control"] = _CACHE_CONTROL
        result.append(block)
    return result


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------


def schemas_to_anthropic(
    tool_schemas: list[ToolSchema],
    *,
    prompt_caching: bool,
) -> list[dict[str, Any]]:
    """Convert ``ToolSchema`` list → Anthropic ``tools`` parameter."""
    result: list[dict[str, Any]] = []
    for schema in tool_schemas:
        tool: dict[str, Any] = {
            "name": schema.name,
            "description": schema.description,
            "input_schema": schema.input_schema,
        }
        if prompt_caching and schema.cache:
            tool["cache_control"] = _CACHE_CONTROL
        result.append(tool)
    return result


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def messages_to_anthropic(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert ``Message`` list → Anthropic ``messages`` parameter."""
    return [_message(m) for m in messages]


def _message(message: Message) -> dict[str, Any]:
    if isinstance(message, UserMessage):
        return {
            "role": "user",
            "content": [_user_block(b) for b in message.content],
        }
    if isinstance(message, AssistantMessage):
        return {
            "role": "assistant",
            "content": [_assistant_block(b) for b in message.content],
        }
    raise TypeError(f"Unknown message type: {type(message)}")


def _user_block(
    block: TextContent | ImageContent | ToolResultContent,
) -> dict[str, Any]:
    if isinstance(block, TextContent):
        return {"type": "text", "text": block.text}
    if isinstance(block, ImageContent):
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": block.media_type,
                "data": block.data_base64,
            },
        }
    if isinstance(block, ToolResultContent):
        result: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "is_error": block.is_error,
        }
        if isinstance(block.content, str):
            result["content"] = block.content
        else:
            result["content"] = [
                {"type": "text", "text": b.text}
                if isinstance(b, TextContent)
                else {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": b.media_type,
                        "data": b.data_base64,
                    },
                }
                for b in block.content
            ]
        return result
    raise TypeError(f"Unknown user content block: {type(block)}")


def _assistant_block(block: TextContent | ToolUseContent | ThinkingContent) -> dict[str, Any]:
    if isinstance(block, TextContent):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseContent):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    if isinstance(block, ThinkingContent):
        return {
            "type": "thinking",
            "thinking": block.thinking,
            "signature": block.signature,
        }
    raise TypeError(f"Unknown assistant content block: {type(block)}")
