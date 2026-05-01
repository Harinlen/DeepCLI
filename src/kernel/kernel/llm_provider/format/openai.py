"""Convert universal types → OpenAI Chat Completions API format.

All functions are pure — no SDK imports, no I/O.

Key differences from Anthropic format
--------------------------------------
- System prompt → single ``{"role": "system", "content": "..."}`` message.
- ``ToolResultContent`` → standalone ``{"role": "tool", ...}`` message.
- Tool schemas → ``{"type": "function", "function": {...}}`` wrapper.
- ``cache`` flags on PromptSection / ToolSchema are silently ignored.
"""

from __future__ import annotations

import orjson
from typing import Any

from kernel.llm.types import (
    AssistantMessage,
    ImageContent,
    Message,
    PromptSection,
    TextContent,
    ToolResultContent,
    ToolSchema,
    ToolUseContent,
    UserMessage,
)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def sections_to_openai_system(sections: list[PromptSection]) -> str:
    """Join ``PromptSection`` list into a single system string."""
    return "\n\n".join(s.text for s in sections)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------


def schemas_to_openai(tool_schemas: list[ToolSchema]) -> list[dict[str, Any]]:
    """Convert ``ToolSchema`` list → OpenAI ``tools`` parameter."""
    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": s.input_schema,
            },
        }
        for s in tool_schemas
    ]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def messages_to_openai(
    messages: list[Message],
    system: list[PromptSection],
) -> list[dict[str, Any]]:
    """Convert ``Message`` list → OpenAI ``messages`` parameter.

    Prepends the system prompt as a ``system`` role message.
    ``ToolResultContent`` blocks (embedded in ``UserMessage``) are split
    out as standalone ``tool`` role messages before any remaining content.
    """
    result: list[dict[str, Any]] = []

    system_text = sections_to_openai_system(system)
    if system_text:
        result.append({"role": "system", "content": system_text})

    for message in messages:
        result.extend(_message(message))

    return result


def _message(message: Message) -> list[dict[str, Any]]:
    if isinstance(message, AssistantMessage):
        return [_assistant_message(message)]
    if isinstance(message, UserMessage):
        return _user_message(message)
    raise TypeError(f"Unknown message type: {type(message)}")


def _assistant_message(message: AssistantMessage) -> dict[str, Any]:
    tool_calls: list[dict[str, Any]] = []
    text_parts: list[str] = []

    for block in message.content:
        if isinstance(block, TextContent):
            text_parts.append(block.text)
        elif isinstance(block, ToolUseContent):
            tool_calls.append(
                {
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": orjson.dumps(block.input).decode(),
                    },
                }
            )

    msg: dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(text_parts) if text_parts else None,
    }
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _user_message(message: UserMessage) -> list[dict[str, Any]]:
    """Split tool results out as ``tool`` role messages.

    Tool result messages come first (they reference the preceding
    assistant tool_calls), then any remaining text/image as a user message.
    """
    tool_msgs: list[dict[str, Any]] = []
    other_blocks: list[TextContent | ImageContent] = []

    for block in message.content:
        if isinstance(block, ToolResultContent):
            content_str = (
                block.content
                if isinstance(block.content, str)
                else "\n".join(
                    b.text if isinstance(b, TextContent) else f"[image:{b.media_type}]"
                    for b in block.content
                )
            )
            tool_msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": block.tool_use_id,
                    "content": content_str,
                }
            )
        else:
            other_blocks.append(block)  # type: ignore[arg-type]

    result: list[dict[str, Any]] = list(tool_msgs)

    if other_blocks:
        content_parts: list[dict[str, Any]] = []
        for block in other_blocks:
            if isinstance(block, TextContent):
                content_parts.append({"type": "text", "text": block.text})
            elif isinstance(block, ImageContent):
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{block.media_type};base64,{block.data_base64}"},
                    }
                )
        result.append({"role": "user", "content": content_parts})

    return result
