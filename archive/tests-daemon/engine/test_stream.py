"""Tests for stream event types."""

from daemon.engine.stream import (
    StreamEnd,
    StreamError,
    TextDelta,
    ThinkingDelta,
    ToolCallResult,
    ToolCallStart,
    UsageInfo,
)
# Importing providers.base resolves the ToolCallResult.image_parts
# forward reference (model_rebuild lives at the bottom of providers/base.py).
from daemon.providers.base import ImageContent  # noqa: F401


class TestStreamEvents:
    def test_thinking_delta(self):
        e = ThinkingDelta(content="let me think...")
        assert e.type == "thinking_delta"
        assert e.content == "let me think..."

    def test_thinking_delta_serialization(self):
        e = ThinkingDelta(content="reasoning step")
        data = e.model_dump()
        assert data == {"type": "thinking_delta", "content": "reasoning step"}
        restored = ThinkingDelta.model_validate(data)
        assert restored == e

    def test_text_delta(self):
        e = TextDelta(content="hello")
        assert e.type == "text_delta"
        assert e.content == "hello"

    def test_tool_call_start(self):
        e = ToolCallStart(
            tool_call_id="tc_1",
            tool_name="bash",
            arguments={"command": "ls"},
        )
        assert e.type == "tool_call_start"
        assert e.tool_name == "bash"
        assert e.arguments == {"command": "ls"}

    def test_tool_call_result(self):
        e = ToolCallResult(
            tool_call_id="tc_1",
            tool_name="bash",
            output="file.txt",
        )
        assert e.type == "tool_call_result"
        assert not e.is_error

    def test_tool_call_result_error(self):
        e = ToolCallResult(
            tool_call_id="tc_1",
            tool_name="bash",
            output="command not found",
            is_error=True,
        )
        assert e.is_error

    def test_tool_call_result_image_parts_default_none(self):
        e = ToolCallResult(
            tool_call_id="tc_1",
            tool_name="browser",
            output="ok",
        )
        assert e.image_parts is None

    def test_tool_call_result_with_image_parts(self):
        img = ImageContent(
            media_type="image/png",
            data_base64="",
            source_sha256="abc123",
        )
        e = ToolCallResult(
            tool_call_id="tc_1",
            tool_name="browser",
            output="Screenshot captured.",
            image_parts=[img],
        )
        assert e.image_parts is not None
        assert len(e.image_parts) == 1
        assert e.image_parts[0].source_sha256 == "abc123"

    def test_tool_call_result_image_parts_serialise(self):
        """Round-trip image_parts through model_dump_json so the wire
        carries them to the TUI."""
        import json

        img = ImageContent(
            media_type="image/png",
            data_base64="",
            source_sha256="deadbeef",
            source_path="/tmp/x.png",
        )
        e = ToolCallResult(
            tool_call_id="tc_1",
            tool_name="browser",
            output="Screenshot captured.",
            image_parts=[img],
        )
        as_json = e.model_dump_json()
        wire = json.loads(as_json)
        assert wire["image_parts"][0]["source_sha256"] == "deadbeef"
        assert wire["image_parts"][0]["media_type"] == "image/png"
        # data_base64 is included but empty (TUI loads from disk).
        assert wire["image_parts"][0]["data_base64"] == ""

    def test_stream_end_default_usage(self):
        e = StreamEnd()
        assert e.type == "end"
        assert e.usage.input_tokens == 0
        assert e.usage.output_tokens == 0

    def test_stream_end_with_usage(self):
        e = StreamEnd(usage=UsageInfo(input_tokens=100, output_tokens=50))
        assert e.usage.input_tokens == 100

    def test_stream_error(self):
        e = StreamError(message="rate limit")
        assert e.type == "error"
        assert e.message == "rate limit"

    def test_serialization_roundtrip(self):
        """Events serialize to JSON and back correctly."""
        e = ToolCallStart(
            tool_call_id="tc_1",
            tool_name="file_read",
            arguments={"path": "/tmp/test"},
        )
        data = e.model_dump()
        assert data["type"] == "tool_call_start"
        restored = ToolCallStart.model_validate(data)
        assert restored == e
