"""ACP wire-format content-block types."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import Field

from kernel.protocol.acp.schemas.base import AcpModel


class AcpTextBlock(AcpModel):
    type: Literal["text"] = "text"
    text: str
    meta: dict[str, Any] | None = None


class AcpImageBlock(AcpModel):
    type: Literal["image"] = "image"
    data: str
    mime_type: str
    meta: dict[str, Any] | None = None


class AcpResourceLinkBlock(AcpModel):
    type: Literal["resource_link"] = "resource_link"
    uri: str
    mime_type: str | None = None
    name: str | None = None
    meta: dict[str, Any] | None = None


class AcpEmbeddedResource(AcpModel):
    uri: str
    mime_type: str | None = None
    text: str | None = None
    blob: str | None = None


class AcpResourceBlock(AcpModel):
    type: Literal["resource"] = "resource"
    resource: AcpEmbeddedResource
    meta: dict[str, Any] | None = None


AcpContentBlock = Annotated[
    Union[AcpTextBlock, AcpImageBlock, AcpResourceLinkBlock, AcpResourceBlock],
    Field(discriminator="type"),
]
