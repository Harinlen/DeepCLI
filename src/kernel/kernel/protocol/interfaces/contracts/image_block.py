"""Image content block — base64-encoded image data."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ImageBlock(BaseModel):
    """An image provided as base64-encoded data with a MIME type."""

    type: Literal["image"] = "image"
    data: str
    """Base64-encoded image data."""
    mime_type: str
    """MIME type, e.g. ``"image/png"``."""
