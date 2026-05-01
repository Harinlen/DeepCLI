"""Resource content block — embedded resource with inline content."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ResourceBlock(BaseModel):
    """An external resource with its content embedded inline.

    Preferred over
    :class:`~kernel.protocol.interfaces.contracts.resource_link_block.ResourceLinkBlock`
    when the content is available at prompt-build time, because it
    avoids an extra round-trip for the agent to fetch the content.
    Requires ``promptCapabilities.embeddedContext: true``.
    """

    type: Literal["resource"] = "resource"
    uri: str
    """Absolute URI identifying the resource."""
    mime_type: str | None = None
    text: str | None = None
    """Text content (used when ``mime_type`` is text-based)."""
    blob: str | None = None
    """Base64-encoded binary content (used for non-text resources)."""
