"""Translators between Mustang universal types and Anthropic content blocks.

The Anthropic Messages API uses a ``content: list[block]`` shape on
every message, where each block is one of ``text`` / ``image`` /
``tool_use`` / ``tool_result``.  This module keeps that shape
isolated from :class:`AnthropicProvider` so the provider class stays
focused on streaming.

Differences from the OpenAI translator:

- System prompt is a top-level parameter, not a role.
- Tool results are ``user``-role messages that carry a single
  ``tool_result`` block referencing the matching ``tool_use_id``.
- Images live inside the same ``content`` array as text, as a
  ``{"type": "image", "source": {...}}`` block.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from daemon.providers.base import (
    ImageContent,
    Message,
    TextContent,
    ToolDefinition,
    ToolResultContent,
    ToolUseContent,
)

if TYPE_CHECKING:
    from daemon.engine.context import PromptSection


# Maximum number of cache_control breakpoints Anthropic allows.
_MAX_CACHE_BREAKPOINTS = 4


def system_to_anthropic(
    sections: list[PromptSection],
    *,
    prompt_caching: bool = True,
) -> list[dict[str, Any]] | str:
    """Convert structured prompt sections to Anthropic system format.

    When *prompt_caching* is enabled, cacheable sections receive
    ``cache_control`` markers.  The function merges adjacent cacheable
    sections to stay within Anthropic's breakpoint limit
    (currently 4).

    Returns a ``list[dict]`` of text blocks when caching is active
    (Anthropic's structured ``system`` format), or a plain ``str``
    when caching is disabled (single text block).
    """
    if not prompt_caching:
        return "\n\n".join(s.text for s in sections)

    # Build text blocks, marking the *last* cacheable section in each
    # contiguous cacheable run with cache_control.  This minimises
    # the number of breakpoints while maximising cached coverage.
    blocks: list[dict[str, Any]] = []
    cache_count = 0

    for i, section in enumerate(sections):
        block: dict[str, Any] = {"type": "text", "text": section.text}

        if section.cacheable and cache_count < _MAX_CACHE_BREAKPOINTS:
            # Only place the marker on the last section in a
            # contiguous cacheable run (next is non-cacheable or end).
            next_cacheable = i + 1 < len(sections) and sections[i + 1].cacheable
            if not next_cacheable:
                block["cache_control"] = {"type": "ephemeral"}
                cache_count += 1

        blocks.append(block)

    return blocks


def _image_block(img: ImageContent) -> dict[str, Any]:
    """Serialise an :class:`ImageContent` to an Anthropic ``image`` block."""
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": img.media_type,
            "data": img.data_base64,
        },
    }


def messages_to_anthropic(
    messages: list[Message],
) -> list[dict[str, Any]]:
    """Translate universal :class:`Message` list to Anthropic messages.

    ``system`` is *not* produced here — the caller passes it as a
    top-level parameter to ``client.messages.create(system=...)``.
    """
    result: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "user":
            blocks: list[dict[str, Any]] = []
            for c in msg.content:
                if isinstance(c, TextContent):
                    blocks.append({"type": "text", "text": c.text})
                elif isinstance(c, ImageContent):
                    blocks.append(_image_block(c))
            if blocks:
                result.append({"role": "user", "content": blocks})

        elif msg.role == "assistant":
            blocks = []
            for c in msg.content:
                if isinstance(c, TextContent):
                    blocks.append({"type": "text", "text": c.text})
                elif isinstance(c, ToolUseContent):
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": c.tool_call_id,
                            "name": c.name,
                            "input": c.arguments,
                        }
                    )
            if blocks:
                result.append({"role": "assistant", "content": blocks})

        elif msg.role == "tool":
            # Anthropic expresses tool results as user-role messages
            # carrying a tool_result block.  Collapse each ToolResultContent
            # into its own user message (matches multi-tool parallel
            # patterns in Claude Code).
            for c in msg.content:
                if not isinstance(c, ToolResultContent):
                    continue
                content_blocks: list[dict[str, Any]] = [{"type": "text", "text": c.output}]
                if c.image_parts:
                    content_blocks.extend(_image_block(img) for img in c.image_parts)
                tr_block = {
                    "type": "tool_result",
                    "tool_use_id": c.tool_call_id,
                    "content": content_blocks,
                    "is_error": c.is_error,
                }
                result.append({"role": "user", "content": [tr_block]})

    return result


def tools_to_anthropic(
    tools: list[ToolDefinition],
    *,
    cache_tools: bool = True,
) -> list[dict[str, Any]]:
    """Translate universal :class:`ToolDefinition` list to Anthropic ``tools``.

    When *cache_tools* is ``True``, the last tool definition receives a
    ``cache_control`` marker.  Tool definitions are semi-static (only
    change on skill activation / MCP reconnect), so caching them avoids
    re-tokenising the full tool schema each round.
    """
    result = [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters,
        }
        for t in tools
    ]
    if cache_tools and result:
        result[-1]["cache_control"] = {"type": "ephemeral"}
    return result


__all__ = ["messages_to_anthropic", "system_to_anthropic", "tools_to_anthropic"]
