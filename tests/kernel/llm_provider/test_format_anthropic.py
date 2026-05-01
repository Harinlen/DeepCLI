"""Tests for kernel.llm_provider.format.anthropic — pure format conversion."""

from __future__ import annotations


from kernel.llm_provider.format.anthropic import (
    messages_to_anthropic,
    schemas_to_anthropic,
    sections_to_anthropic,
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
# sections_to_anthropic
# ---------------------------------------------------------------------------


class TestSectionsToAnthropic:
    def test_single_plain_section(self):
        result = sections_to_anthropic([PromptSection(text="Be helpful.")], prompt_caching=False)
        assert result == [{"type": "text", "text": "Be helpful."}]

    def test_cache_ignored_when_disabled(self):
        result = sections_to_anthropic([PromptSection(text="x", cache=True)], prompt_caching=False)
        assert "cache_control" not in result[0]

    def test_cache_applied_when_enabled(self):
        result = sections_to_anthropic([PromptSection(text="x", cache=True)], prompt_caching=True)
        assert result[0]["cache_control"] == {"type": "ephemeral"}

    def test_cache_only_on_marked_sections(self):
        sections = [
            PromptSection(text="no cache"),
            PromptSection(text="yes cache", cache=True),
        ]
        result = sections_to_anthropic(sections, prompt_caching=True)
        assert "cache_control" not in result[0]
        assert result[1]["cache_control"] == {"type": "ephemeral"}

    def test_empty(self):
        assert sections_to_anthropic([], prompt_caching=True) == []


# ---------------------------------------------------------------------------
# schemas_to_anthropic
# ---------------------------------------------------------------------------


class TestSchemasToAnthropic:
    def test_basic_schema(self):
        result = schemas_to_anthropic(
            [ToolSchema(name="bash", description="run cmd", input_schema={"type": "object"})],
            prompt_caching=False,
        )
        assert result[0]["name"] == "bash"
        assert "cache_control" not in result[0]

    def test_cache_applied(self):
        result = schemas_to_anthropic(
            [ToolSchema(name="t", description="d", input_schema={}, cache=True)],
            prompt_caching=True,
        )
        assert result[0]["cache_control"] == {"type": "ephemeral"}

    def test_cache_ignored_when_disabled(self):
        result = schemas_to_anthropic(
            [ToolSchema(name="t", description="d", input_schema={}, cache=True)],
            prompt_caching=False,
        )
        assert "cache_control" not in result[0]


# ---------------------------------------------------------------------------
# messages_to_anthropic
# ---------------------------------------------------------------------------


class TestMessagesToAnthropic:
    def test_user_text(self):
        result = messages_to_anthropic([UserMessage([TextContent(text="hi")])])
        assert result == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]

    def test_assistant_text(self):
        result = messages_to_anthropic([AssistantMessage([TextContent(text="hello")])])
        assert result[0]["role"] == "assistant"
        assert result[0]["content"][0] == {"type": "text", "text": "hello"}

    def test_assistant_tool_use(self):
        result = messages_to_anthropic(
            [AssistantMessage([ToolUseContent(id="tu_1", name="bash", input={"cmd": "ls"})])]
        )
        block = result[0]["content"][0]
        assert block == {"type": "tool_use", "id": "tu_1", "name": "bash", "input": {"cmd": "ls"}}

    def test_tool_result_string(self):
        result = messages_to_anthropic(
            [UserMessage([ToolResultContent(tool_use_id="tu_1", content="out")])]
        )
        block = result[0]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "tu_1"
        assert block["content"] == "out"
        assert block["is_error"] is False

    def test_tool_result_error_flag(self):
        result = messages_to_anthropic(
            [UserMessage([ToolResultContent(tool_use_id="tu_1", content="err", is_error=True)])]
        )
        assert result[0]["content"][0]["is_error"] is True

    def test_tool_result_list_content(self):
        result = messages_to_anthropic(
            [
                UserMessage(
                    [
                        ToolResultContent(
                            tool_use_id="tu_1",
                            content=[TextContent(text="line1"), TextContent(text="line2")],
                        )
                    ]
                )
            ]
        )
        inner = result[0]["content"][0]["content"]
        assert isinstance(inner, list)
        assert inner[0] == {"type": "text", "text": "line1"}

    def test_image_content(self):
        result = messages_to_anthropic(
            [UserMessage([ImageContent(media_type="image/png", data_base64="abc")])]
        )
        block = result[0]["content"][0]
        assert block["type"] == "image"
        assert block["source"]["type"] == "base64"
        assert block["source"]["data"] == "abc"

    def test_full_turn_sequence(self):
        messages = [
            UserMessage([TextContent(text="list files")]),
            AssistantMessage(
                [
                    TextContent(text="OK."),
                    ToolUseContent(id="tu_1", name="bash", input={"cmd": "ls"}),
                ]
            ),
            UserMessage([ToolResultContent(tool_use_id="tu_1", content="a.txt")]),
        ]
        result = messages_to_anthropic(messages)
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"
