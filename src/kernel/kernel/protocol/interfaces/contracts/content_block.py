"""ContentBlock — discriminated union of all supported content variants.

Used as the element type of ``session/prompt`` prompts and inside
tool-call result payloads.  The ``type`` field is the discriminator.
"""

from __future__ import annotations

from typing import Annotated, Union

from pydantic import Field

from kernel.protocol.interfaces.contracts.image_block import ImageBlock
from kernel.protocol.interfaces.contracts.resource_block import ResourceBlock
from kernel.protocol.interfaces.contracts.resource_link_block import (
    ResourceLinkBlock,
)
from kernel.protocol.interfaces.contracts.text_block import TextBlock

ContentBlock = Annotated[
    Union[TextBlock, ImageBlock, ResourceLinkBlock, ResourceBlock],
    Field(discriminator="type"),
]
"""Discriminated union of content variants.

All agents MUST support :class:`TextBlock` and
:class:`ResourceLinkBlock`.  :class:`ImageBlock` requires
``promptCapabilities.image: true`` and :class:`ResourceBlock` requires
``promptCapabilities.embeddedContext: true``.
"""
