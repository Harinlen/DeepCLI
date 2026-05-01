"""Text content block — plain UTF-8 text."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class TextBlock(BaseModel):
    """A plain-text content fragment."""

    type: Literal["text"] = "text"
    text: str
