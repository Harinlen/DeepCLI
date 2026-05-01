"""Resource-link content block — a URI reference without embedded content."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ResourceLinkBlock(BaseModel):
    """A reference to an external resource by URI.

    Unlike :class:`~kernel.protocol.interfaces.contracts.resource_block.ResourceBlock`,
    this variant carries only a pointer; the agent must resolve the
    URI itself if it needs the actual content.  All agents MUST support
    this variant in ``session/prompt`` prompts.
    """

    type: Literal["resource_link"] = "resource_link"
    uri: str
    """Absolute URI of the resource (e.g. ``file:///home/user/main.py``)."""
    mime_type: str | None = None
    name: str | None = None
