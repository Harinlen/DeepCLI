"""Tests for kernel.llm_provider.format.openai — pure format conversion."""

from __future__ import annotations

import orjson

from kernel.llm_provider.format.openai import (
    messages_to_openai,
    schemas_to_openai,
    sections_to_openai_system,
)
from kernel.llm.types import (
    AssistantMessage,
    ImageContent,
    PromptSection,
    TextContent,
    ToolResultContent,
    ToolSchema,
    ToolUseContent,
    UserMessage,
)


# ---------------------------------------------------------------------------
# sections_to_openai_system
# ---------------------------------------------------------------------------


class TestSectionsToOpenAISystem:
    def test_single(self):
        assert sections_to_openai_system([PromptSection(text="Be helpful.")]) == "Be helpful."

    def test_multiple_joined(self):
        result = sections_to_openai_system(
            [
                PromptSection(text="Part 1."),
                PromptSection(text="Part 2."),
            ]
        )
        assert result == "Part 1.\n\nPart 2."

    def test_cache_ignored(self):
        result = sections_to_openai_system([PromptSection(text="x", cache=True)])
        assert result == "x"

    def test_empty(self):
        assert sections_to_openai_system([]) == ""


# ---------------------------------------------------------------------------
# schemas_to_openai
# ---------------------------------------------------------------------------


class TestSchemasToOpenAI:
    def test_basic(self):
        result = schemas_to_openai(
            [ToolSchema(name="bash", description="run", input_schema={"type": "object"})]
        )
        assert result == [
            {
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "run",
                    "parameters": {"type": "object"},
                },
            }
        ]

    def test_cache_ignored(self):
        result = schemas_to_openai(
            [ToolSchema(name="t", description="d", input_schema={}, cache=True)]
        )
        assert "cache_control" not in result[0]
        assert "cache_control" not in result[0]["function"]


# ---------------------------------------------------------------------------
# messages_to_openai
# ---------------------------------------------------------------------------


class TestMessagesToOpenAI:
    def test_system_prepended(self):
        result = messages_to_openai(
            [UserMessage([TextContent(text="hi")])],
            [PromptSection(text="sys")],
        )
        assert result[0] == {"role": "system", "content": "sys"}

    def test_no_system_when_empty(self):
        result = messages_to_openai([UserMessage([TextContent(text="hi")])], [])
        assert result[0]["role"] == "user"

    def test_user_text(self):
        result = messages_to_openai([UserMessage([TextContent(text="hello")])], [])
        assert result[0] == {"role": "user", "content": [{"type": "text", "text": "hello"}]}

    def test_user_image(self):
        result = messages_to_openai(
            [UserMessage([ImageContent(media_type="image/jpeg", data_base64="xyz")])], []
        )
        block = result[0]["content"][0]
        assert block["type"] == "image_url"
        assert "data:image/jpeg;base64,xyz" in block["image_url"]["url"]

    def test_assistant_text(self):
        result = messages_to_openai([AssistantMessage([TextContent(text="response")])], [])
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "response"

    def test_assistant_tool_call(self):
        result = messages_to_openai(
            [AssistantMessage([ToolUseContent(id="call_1", name="bash", input={"cmd": "ls"})])], []
        )
        msg = result[0]
        assert msg["role"] == "assistant"
        assert msg["content"] is None
        assert msg["tool_calls"][0] == {
            "id": "call_1",
            "type": "function",
            "function": {"name": "bash", "arguments": orjson.dumps({"cmd": "ls"}).decode()},
        }

    def test_tool_result_becomes_tool_role(self):
        result = messages_to_openai(
            [UserMessage([ToolResultContent(tool_use_id="call_1", content="output")])], []
        )
        assert result[0] == {"role": "tool", "tool_call_id": "call_1", "content": "output"}

    def test_tool_result_list_content_stringified(self):
        result = messages_to_openai(
            [
                UserMessage(
                    [
                        ToolResultContent(
                            tool_use_id="call_1",
                            content=[TextContent(text="p1"), TextContent(text="p2")],
                        )
                    ]
                )
            ],
            [],
        )
        assert result[0]["content"] == "p1\np2"

    def test_mixed_user_message_split(self):
        """Tool result + text in same UserMessage → tool msg then user msg."""
        result = messages_to_openai(
            [
                UserMessage(
                    [
                        ToolResultContent(tool_use_id="c1", content="done"),
                        TextContent(text="follow up"),
                    ]
                )
            ],
            [],
        )
        roles = [m["role"] for m in result]
        assert "tool" in roles
        assert "user" in roles
        # tool comes first
        assert roles.index("tool") < roles.index("user")

    def test_full_conversation(self):
        result = messages_to_openai(
            [
                UserMessage([TextContent(text="list files")]),
                AssistantMessage([ToolUseContent(id="c1", name="bash", input={"cmd": "ls"})]),
                UserMessage([ToolResultContent(tool_use_id="c1", content="a.txt")]),
                AssistantMessage([TextContent(text="Found a.txt.")]),
            ],
            [PromptSection(text="sys")],
        )
        # system + user + assistant + tool + assistant = 5
        assert len(result) == 5
        assert result[0]["role"] == "system"
        assert result[-1]["content"] == "Found a.txt."
