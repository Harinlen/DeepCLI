"""Tests for OpenAI-compatible provider — message translation logic."""

import json

from daemon.providers.base import Message, ToolDefinition
from daemon.providers.openai_compatible import OpenAICompatibleProvider


class TestToOpenAIMessages:
    """Test universal Message → OpenAI format translation."""

    def test_user_message(self):
        msgs = [Message.user("hello")]
        result = OpenAICompatibleProvider._to_openai_messages(msgs)
        assert result == [{"role": "user", "content": "hello"}]

    def test_system_prompt_prepended(self):
        msgs = [Message.user("hi")]
        result = OpenAICompatibleProvider._to_openai_messages(msgs, system="You are helpful.")
        assert result[0] == {"role": "system", "content": "You are helpful."}
        assert result[1] == {"role": "user", "content": "hi"}

    def test_assistant_text(self):
        msgs = [Message.assistant_text("sure")]
        result = OpenAICompatibleProvider._to_openai_messages(msgs)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "sure"

    def test_assistant_tool_use(self):
        msgs = [Message.assistant_tool_use("tc_1", "bash", {"command": "ls"})]
        result = OpenAICompatibleProvider._to_openai_messages(msgs)
        msg = result[0]
        assert msg["role"] == "assistant"
        assert len(msg["tool_calls"]) == 1
        tc = msg["tool_calls"][0]
        assert tc["id"] == "tc_1"
        assert tc["function"]["name"] == "bash"
        assert json.loads(tc["function"]["arguments"]) == {"command": "ls"}

    def test_tool_result(self):
        msgs = [Message.tool_result("tc_1", "file.txt")]
        result = OpenAICompatibleProvider._to_openai_messages(msgs)
        assert result[0] == {
            "role": "tool",
            "tool_call_id": "tc_1",
            "content": "file.txt",
        }

    def test_full_conversation_roundtrip(self):
        """Multi-turn conversation translates correctly."""
        msgs = [
            Message.user("list files"),
            Message.assistant_tool_use("tc_1", "bash", {"command": "ls"}),
            Message.tool_result("tc_1", "a.py\nb.py"),
            Message.assistant_text("Found a.py and b.py"),
        ]
        result = OpenAICompatibleProvider._to_openai_messages(msgs, system="Be helpful")
        assert len(result) == 5  # system + 4 messages
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"
        assert "tool_calls" in result[2]
        assert result[3]["role"] == "tool"
        assert result[4]["role"] == "assistant"
        assert result[4]["content"] == "Found a.py and b.py"


class TestToOpenAITools:
    def test_tool_definition_translation(self):
        tools = [
            ToolDefinition(
                name="bash",
                description="Run a command",
                parameters={
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                },
            )
        ]
        result = OpenAICompatibleProvider._to_openai_tools(tools)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "bash"
        assert result[0]["function"]["description"] == "Run a command"
