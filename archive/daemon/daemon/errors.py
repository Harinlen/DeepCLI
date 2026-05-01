"""Domain exceptions for Mustang daemon."""


class MustangError(Exception):
    """Base exception for all Mustang errors."""


class ConfigError(MustangError):
    """Configuration loading or validation failed."""


class ProviderError(MustangError):
    """LLM provider error (API call, streaming, etc.)."""


class PromptTooLongError(ProviderError):
    """Context window exceeded — prompt is too long for the model.

    Raised by providers when the API returns a ``prompt_too_long`` or
    equivalent error.  The orchestrator catches this and triggers
    reactive compaction instead of terminating the query.

    Attributes:
        tokens_over: Estimated excess tokens (if parseable), else ``None``.
    """

    def __init__(self, message: str, *, tokens_over: int | None = None) -> None:
        super().__init__(message)
        self.tokens_over = tokens_over


class ProviderNotFoundError(ProviderError):
    """Requested provider is not registered."""


class ModelResolveError(ProviderError):
    """Could not resolve a model reference to a provider."""


class ToolExecutionError(MustangError):
    """A tool failed during execution."""


class McpError(MustangError):
    """MCP protocol or transport error."""
