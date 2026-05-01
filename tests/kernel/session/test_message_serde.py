"""Tests for kernel.session.message_serde — Message round-trip serialization."""

from __future__ import annotations

import pytest

from kernel.llm.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
    UserMessage,
)
from kernel.session.message_serde import deserialize_message, serialize_message


# ---------------------------------------------------------------------------
# Round-trip: serialize → deserialize should produce equal objects
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_user_message_text(self) -> None:
        msg = UserMessage(content=[TextContent(text="hello world")])
        assert deserialize_message(serialize_message(msg)) == msg

    def test_user_message_image(self) -> None:
        msg = UserMessage(
            content=[
                ImageContent(media_type="image/png", data_base64="iVBOR..."),
            ]
        )
        assert deserialize_message(serialize_message(msg)) == msg

    def test_user_message_tool_result_str(self) -> None:
        msg = UserMessage(
            content=[
                ToolResultContent(
                    tool_use_id="tu_abc",
                    content="file contents here",
                    is_error=False,
                ),
            ]
        )
        assert deserialize_message(serialize_message(msg)) == msg

    def test_user_message_tool_result_list(self) -> None:
        msg = UserMessage(
            content=[
                ToolResultContent(
                    tool_use_id="tu_def",
                    content=[
                        TextContent(text="line 1"),
                        ImageContent(media_type="image/jpeg", data_base64="abc"),
                    ],
                    is_error=True,
                ),
            ]
        )
        assert deserialize_message(serialize_message(msg)) == msg

    def test_assistant_message_text(self) -> None:
        msg = AssistantMessage(content=[TextContent(text="Sure, I can help.")])
        assert deserialize_message(serialize_message(msg)) == msg

    def test_assistant_message_tool_use(self) -> None:
        msg = AssistantMessage(
            content=[
                ToolUseContent(
                    id="tu_123",
                    name="Read",
                    input={"file_path": "/tmp/foo.py"},
                ),
            ]
        )
        assert deserialize_message(serialize_message(msg)) == msg

    def test_assistant_message_thinking(self) -> None:
        msg = AssistantMessage(
            content=[
                ThinkingContent(
                    thinking="Let me consider the options...",
                    signature="sig_abc123",
                ),
                TextContent(text="I think option A is better."),
            ]
        )
        assert deserialize_message(serialize_message(msg)) == msg

    def test_assistant_message_mixed(self) -> None:
        msg = AssistantMessage(
            content=[
                ThinkingContent(thinking="hmm", signature="sig"),
                TextContent(text="I'll read the file."),
                ToolUseContent(id="tu_1", name="Read", input={"file_path": "/a"}),
                ToolUseContent(id="tu_2", name="Bash", input={"command": "ls"}),
            ]
        )
        assert deserialize_message(serialize_message(msg)) == msg

    def test_multiple_user_content_blocks(self) -> None:
        msg = UserMessage(
            content=[
                TextContent(text="Here's the result:"),
                ToolResultContent(tool_use_id="tu_x", content="ok", is_error=False),
                ToolResultContent(tool_use_id="tu_y", content="err", is_error=True),
            ]
        )
        assert deserialize_message(serialize_message(msg)) == msg


# ---------------------------------------------------------------------------
# Deserialize edge cases
# ---------------------------------------------------------------------------


class TestDeserializeEdgeCases:
    def test_unknown_role_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown message role"):
            deserialize_message({"role": "system", "content": []})

    def test_unknown_user_content_type_falls_back_to_text(self) -> None:
        data = {"role": "user", "content": [{"type": "video", "url": "x"}]}
        msg = deserialize_message(data)
        assert isinstance(msg, UserMessage)
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], TextContent)

    def test_unknown_assistant_content_type_falls_back_to_text(self) -> None:
        data = {"role": "assistant", "content": [{"type": "citation", "ref": "x"}]}
        msg = deserialize_message(data)
        assert isinstance(msg, AssistantMessage)
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], TextContent)

    def test_tool_result_with_empty_string_content(self) -> None:
        msg = UserMessage(
            content=[
                ToolResultContent(tool_use_id="tu_z", content="", is_error=False),
            ]
        )
        assert deserialize_message(serialize_message(msg)) == msg


# ---------------------------------------------------------------------------
# Serialize output structure
# ---------------------------------------------------------------------------


class TestSerializeStructure:
    def test_user_message_has_role_field(self) -> None:
        data = serialize_message(UserMessage(content=[TextContent(text="hi")]))
        assert data["role"] == "user"
        assert data["content"][0]["type"] == "text"
        assert data["content"][0]["text"] == "hi"

    def test_assistant_message_has_role_field(self) -> None:
        data = serialize_message(
            AssistantMessage(content=[TextContent(text="ok")])
        )
        assert data["role"] == "assistant"

    def test_tool_use_content_structure(self) -> None:
        data = serialize_message(
            AssistantMessage(
                content=[
                    ToolUseContent(id="tu_1", name="Read", input={"a": 1}),
                ]
            )
        )
        block = data["content"][0]
        assert block["type"] == "tool_use"
        assert block["id"] == "tu_1"
        assert block["name"] == "Read"
        assert block["input"] == {"a": 1}
