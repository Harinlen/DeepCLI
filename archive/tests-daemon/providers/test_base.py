"""Tests for universal message types and Provider ABC."""

from daemon.providers.base import (
    Message,
    ModelInfo,
    TextContent,
    ToolDefinition,
    ToolResultContent,
    ToolUseContent,
)


class TestMessage:
    def test_user_message(self):
        msg = Message.user("hello")
        assert msg.role == "user"
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], TextContent)
        assert msg.content[0].text == "hello"

    def test_assistant_text(self):
        msg = Message.assistant_text("hi there")
        assert msg.role == "assistant"
        assert msg.content[0].text == "hi there"

    def test_assistant_tool_use(self):
        msg = Message.assistant_tool_use("tc_1", "bash", {"command": "ls"})
        assert msg.role == "assistant"
        c = msg.content[0]
        assert isinstance(c, ToolUseContent)
        assert c.tool_call_id == "tc_1"
        assert c.name == "bash"
        assert c.arguments == {"command": "ls"}

    def test_tool_result(self):
        msg = Message.tool_result("tc_1", "output text")
        assert msg.role == "tool"
        c = msg.content[0]
        assert isinstance(c, ToolResultContent)
        assert c.tool_call_id == "tc_1"
        assert c.output == "output text"
        assert not c.is_error

    def test_tool_result_error(self):
        msg = Message.tool_result("tc_1", "failed", is_error=True)
        assert msg.content[0].is_error


class TestToolDefinition:
    def test_tool_definition(self):
        td = ToolDefinition(
            name="bash",
            description="Run a command",
            parameters={"type": "object", "properties": {"command": {"type": "string"}}},
        )
        assert td.name == "bash"
        assert "command" in td.parameters["properties"]


class TestModelInfo:
    def test_model_info_defaults(self):
        m = ModelInfo(id="qwen3.5", name="Qwen 3.5", provider="local")
        assert m.supports_tools is True

    def test_model_info_no_tools(self):
        m = ModelInfo(id="old-model", name="Old", provider="x", supports_tools=False)
        assert m.supports_tools is False
