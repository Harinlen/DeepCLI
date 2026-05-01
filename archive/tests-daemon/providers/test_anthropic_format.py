"""Tests for the Anthropic format translator (Step 5.1)."""

from __future__ import annotations

from daemon.engine.stream import ToolDefinition
from daemon.providers.anthropic_format import (
    messages_to_anthropic,
    tools_to_anthropic,
)
from daemon.providers.base import (
    ImageContent,
    Message,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)


class TestMessagesToAnthropic:
    def test_user_text_only(self) -> None:
        msgs = [Message.user("hello")]
        result = messages_to_anthropic(msgs)
        assert result == [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]

    def test_user_with_image(self) -> None:
        img = ImageContent(media_type="image/png", data_base64="BASE64", source_sha256="abc")
        msg = Message.user("see this", images=[img])
        result = messages_to_anthropic([msg])
        assert result[0]["role"] == "user"
        blocks = result[0]["content"]
        assert blocks[0] == {"type": "text", "text": "see this"}
        assert blocks[1]["type"] == "image"
        assert blocks[1]["source"] == {
            "type": "base64",
            "media_type": "image/png",
            "data": "BASE64",
        }

    def test_assistant_text_and_tool_use(self) -> None:
        msg = Message(
            role="assistant",
            content=[
                TextContent(text="let me check"),
                ToolUseContent(tool_call_id="tc_1", name="bash", arguments={"cmd": "ls"}),
            ],
        )
        result = messages_to_anthropic([msg])
        assert result == [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "let me check"},
                    {
                        "type": "tool_use",
                        "id": "tc_1",
                        "name": "bash",
                        "input": {"cmd": "ls"},
                    },
                ],
            }
        ]

    def test_tool_result_becomes_user_message(self) -> None:
        msg = Message(
            role="tool",
            content=[ToolResultContent(tool_call_id="tc_1", output="total 0", is_error=False)],
        )
        result = messages_to_anthropic([msg])
        assert len(result) == 1
        assert result[0]["role"] == "user"
        tr_block = result[0]["content"][0]
        assert tr_block["type"] == "tool_result"
        assert tr_block["tool_use_id"] == "tc_1"
        assert tr_block["is_error"] is False
        assert tr_block["content"][0] == {"type": "text", "text": "total 0"}

    def test_tool_result_with_images(self) -> None:
        img = ImageContent(media_type="image/png", data_base64="B64", source_sha256="xx")
        msg = Message(
            role="tool",
            content=[
                ToolResultContent(
                    tool_call_id="tc_2",
                    output="[image: image/png]",
                    image_parts=[img],
                )
            ],
        )
        result = messages_to_anthropic([msg])
        tr_block = result[0]["content"][0]
        # First block: text, then the image.
        assert tr_block["content"][0]["type"] == "text"
        assert tr_block["content"][1]["type"] == "image"

    def test_skips_empty_messages(self) -> None:
        result = messages_to_anthropic(
            [Message(role="user", content=[]), Message(role="assistant", content=[])]
        )
        assert result == []


class TestToolsToAnthropic:
    def test_translation_with_cache(self) -> None:
        tool = ToolDefinition(
            name="bash",
            description="run shell commands",
            parameters={"type": "object", "properties": {"cmd": {"type": "string"}}},
        )
        result = tools_to_anthropic([tool])
        assert result == [
            {
                "name": "bash",
                "description": "run shell commands",
                "input_schema": {
                    "type": "object",
                    "properties": {"cmd": {"type": "string"}},
                },
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def test_translation_without_cache(self) -> None:
        tool = ToolDefinition(
            name="bash",
            description="run shell commands",
            parameters={"type": "object", "properties": {"cmd": {"type": "string"}}},
        )
        result = tools_to_anthropic([tool], cache_tools=False)
        assert result == [
            {
                "name": "bash",
                "description": "run shell commands",
                "input_schema": {
                    "type": "object",
                    "properties": {"cmd": {"type": "string"}},
                },
            }
        ]

    def test_cache_only_on_last_tool(self) -> None:
        tools = [
            ToolDefinition(name="a", description="first", parameters={}),
            ToolDefinition(name="b", description="second", parameters={}),
        ]
        result = tools_to_anthropic(tools)
        assert "cache_control" not in result[0]
        assert result[1]["cache_control"] == {"type": "ephemeral"}
