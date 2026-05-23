"""WebBridge extension protocol schemas."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

PROTOCOL_VERSION = "web-bridge.v1"


class BrowserInfo(BaseModel):
    """Browser identity reported by the extension."""

    name: str = "Chrome"
    version: str = ""


class WebBridgeHello(BaseModel):
    """Initial extension handshake."""

    id: str = Field(default_factory=lambda: f"hello-{uuid4().hex}")
    type: Literal["hello"] = "hello"
    protocol_version: str = Field(alias="protocolVersion")
    extension_id: str = Field(alias="extensionId")
    browser: BrowserInfo = Field(default_factory=BrowserInfo)
    pairing_token: str | None = Field(default=None, alias="pairingToken")
    secret: str | None = None


class WebBridgeHelloAck(BaseModel):
    """Kernel response to a successful extension handshake."""

    id: str
    type: Literal["hello_ack"] = "hello_ack"
    ok: bool
    secret: str | None = None
    heartbeat_ms: int = Field(default=15_000, alias="heartbeatMs")
    message: str | None = None


class WebBridgeFetchExtract(BaseModel):
    """Extraction options for a managed-tab fetch."""

    html: bool = False
    text: bool = True
    readability: bool = True
    metadata: bool = True
    screenshot: bool = False


class WebBridgeFetchRequest(BaseModel):
    """Kernel-to-extension managed-tab fetch request."""

    id: str
    type: Literal["fetch_tab"] = "fetch_tab"
    protocol_version: str = Field(default=PROTOCOL_VERSION, alias="protocolVersion")
    url: str
    timeout_ms: int = Field(default=45_000, alias="timeoutMs")
    max_html_bytes: int = Field(default=200_000, alias="maxHtmlBytes")
    max_text_chars: int = Field(default=50_000, alias="maxTextChars")
    extract: WebBridgeFetchExtract = Field(default_factory=WebBridgeFetchExtract)


class WebBridgeSignals(BaseModel):
    """Page-level signals detected by the extension."""

    login_required: bool = Field(default=False, alias="loginRequired")
    captcha_detected: bool = Field(default=False, alias="captchaDetected")
    cookie_banner_seen: bool = Field(default=False, alias="cookieBannerSeen")
    loaded: bool = True
    timed_out: bool = Field(default=False, alias="timedOut")
    closed_tab: bool = Field(default=True, alias="closedTab")
    text_length: int = Field(default=0, alias="textLength")


class WebBridgeFetchResult(BaseModel):
    """Extension-to-kernel managed-tab fetch result."""

    id: str
    type: Literal["fetch_result"] = "fetch_result"
    ok: bool
    url: str | None = None
    final_url: str | None = Field(default=None, alias="finalUrl")
    title: str = ""
    text: str = ""
    readability_text: str = Field(default="", alias="readabilityText")
    html: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    signals: WebBridgeSignals = Field(default_factory=WebBridgeSignals)
    extraction_method: str = Field(default="", alias="extractionMethod")
    error: str | None = None
    message: str | None = None


class WebBridgeStatus(BaseModel):
    """Public WebBridge status exposed to ACP and install page."""

    status: str
    install_url: str = Field(alias="installUrl")
    bridge_ws_url: str = Field(alias="bridgeWsUrl")
    paired: bool
    connected: bool
    protocol_version: str = Field(default=PROTOCOL_VERSION, alias="protocolVersion")
    browser: BrowserInfo | None = None
    message: str | None = None
    pairing_token: str | None = Field(default=None, alias="pairingToken")
    unpacked_path: str = Field(default="", alias="unpackedPath")
    zip_url: str = Field(default="", alias="zipUrl")


__all__ = [
    "PROTOCOL_VERSION",
    "BrowserInfo",
    "WebBridgeFetchExtract",
    "WebBridgeFetchRequest",
    "WebBridgeFetchResult",
    "WebBridgeHello",
    "WebBridgeHelloAck",
    "WebBridgeSignals",
    "WebBridgeStatus",
]
