"""Thinking block assembly for conversation history."""

from __future__ import annotations

from dataclasses import dataclass, field

from kernel.llm.types import ThinkingContent


@dataclass
class ThoughtAccumulator:
    """Collect ThoughtChunk pieces for a single thinking block.

    Anthropic-style thinking can arrive as content deltas followed by a signed
    signature.  The history layer must replay the merged block exactly; dropping
    the signature makes later provider calls fail validation.
    """

    content_parts: list[str] = field(default_factory=list)
    signature: str = ""

    def add_content(self, text: str) -> None:
        """Append non-empty thinking text.

        Args:
            text: Provider chunk content. Empty chunks are ignored because they
                carry no replay value.

        Returns:
            ``None``.
        """
        if text:
            self.content_parts.append(text)

    def add_signature(self, signature: str) -> None:
        """Record the latest provider signature for the thinking block.

        Args:
            signature: Provider-signed marker required when replaying thinking.

        Returns:
            ``None``.
        """
        if signature:
            self.signature = signature

    def build(self) -> ThinkingContent | None:
        """Create a replayable thinking block when any provider data arrived.

        Returns:
            ``ThinkingContent`` with merged text/signature, or ``None`` when no
            thinking content was streamed.
        """
        content = "".join(self.content_parts)
        if not content and not self.signature:
            return None
        return ThinkingContent(thinking=content, signature=self.signature)


def assemble_thinking(thoughts: list[object]) -> ThinkingContent | None:
    """Merge ThoughtChunk events into one ThinkingContent block.

    Args:
        thoughts: Provider-neutral thought chunks captured during streaming.

    Returns:
        A replayable thinking block for conversation history, or ``None`` when
        the provider emitted no thinking payload.
    """
    if not thoughts:
        return None

    acc = ThoughtAccumulator()
    for chunk in thoughts:
        content = getattr(chunk, "content", "")
        signature = getattr(chunk, "signature", "")
        if signature:
            acc.add_signature(signature)
        else:
            acc.add_content(content)
    return acc.build()
