"""Translators between Mustang's universal types and OpenAI chat format.

Pure conversion helpers — no I/O, no state — extracted from
:class:`OpenAIBaseProvider` so the provider class stays focused on
streaming logic.  Every OpenAI-compatible provider shares these
translations.

- :func:`messages_to_openai`: universal :class:`Message` list →
  OpenAI ``messages`` array (inserts the system prompt when given).
- :func:`tools_to_openai`: universal :class:`ToolDefinition` list →
  OpenAI ``tools`` array.
"""

from __future__ import annotations

import json
from typing import Any

from daemon.providers.base import (
    ImageContent,
    Message,
    TextContent,
    ToolDefinition,
    ToolResultContent,
    ToolUseContent,
)


def messages_to_openai(messages: list[Message], system: str | None = None) -> list[dict[str, Any]]:
    """Translate universal :class:`Message` list to OpenAI chat messages.

    Args:
        messages: Universal message list from the orchestrator.
        system: Optional system prompt to prepend.

    Returns:
        List of dicts ready for ``chat.completions.create(messages=…)``.
    """
    result: list[dict[str, Any]] = []

    if system:
        result.append({"role": "system", "content": system})

    for msg in messages:
        if msg.role == "user":
            text_parts = [c.text for c in msg.content if isinstance(c, TextContent)]
            dropped_images = sum(1 for c in msg.content if isinstance(c, ImageContent))
            body = "\n".join(text_parts)
            if dropped_images:
                body = (
                    f"[{dropped_images} image(s) dropped — this provider "
                    f"does not support multimodal input]\n" + body
                )
            result.append({"role": "user", "content": body})

        elif msg.role == "assistant":
            oai_msg: dict[str, Any] = {"role": "assistant"}
            text_parts = []
            tool_calls = []

            for c in msg.content:
                if isinstance(c, TextContent):
                    text_parts.append(c.text)
                elif isinstance(c, ToolUseContent):
                    tool_calls.append(
                        {
                            "id": c.tool_call_id,
                            "type": "function",
                            "function": {
                                "name": c.name,
                                "arguments": json.dumps(c.arguments),
                            },
                        }
                    )

            if text_parts:
                oai_msg["content"] = "\n".join(text_parts)
            if tool_calls:
                oai_msg["tool_calls"] = tool_calls

            result.append(oai_msg)

        elif msg.role == "tool":
            for c in msg.content:
                if isinstance(c, ToolResultContent):
                    content = c.output
                    if c.image_parts:
                        content = (
                            f"[{len(c.image_parts)} image(s) dropped — "
                            f"provider does not support multimodal tool results]\n" + content
                        )
                    result.append(
                        {
                            "role": "tool",
                            "tool_call_id": c.tool_call_id,
                            "content": content,
                        }
                    )

    return result


def tools_to_openai(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Translate universal :class:`ToolDefinition` list to OpenAI tools format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


__all__ = ["messages_to_openai", "tools_to_openai"]
