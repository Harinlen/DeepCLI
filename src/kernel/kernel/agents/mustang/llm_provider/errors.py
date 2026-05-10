"""Provider layer error types.

Unrecoverable errors — raised at Provider construction time or before
``stream()`` starts.  Transient errors (rate limits, temporary outages)
are yielded as ``StreamError`` chunks inside the stream, not raised.
"""

from __future__ import annotations


class ProviderError(RuntimeError):
    """Unrecoverable provider error (bad config, auth failure, etc.).

    Raised by Provider constructors or at the start of ``stream()`` when
    the failure is non-transient and cannot be surfaced as a chunk.
    """


class PromptTooLongError(ProviderError):
    """The assembled prompt exceeds the model's context window.

    The Orchestrator may catch this to trigger compaction before retrying.
    """


class MediaSizeError(ProviderError):
    """The conversation contains image/media blocks that exceed the
    provider's size limit.

    The Orchestrator may catch this to strip images from history and retry.
    """
