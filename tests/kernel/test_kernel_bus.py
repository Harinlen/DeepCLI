from __future__ import annotations

import pytest
from pydantic import ValidationError

from kernel.kernel_bus import BusMessage, BusMessageMeta, service_kind


def test_bus_message_wraps_raw_acp_payload_without_contract_field() -> None:
    message = BusMessage(
        source="client:cli",
        target="resource:web_bridge",
        acp={
            "jsonrpc": "2.0",
            "id": "fetch-1",
            "method": "_mustang.resource/web_bridge.fetch_tab",
            "params": {"url": "https://example.test"},
        },
        meta=BusMessageMeta(correlationId="fetch-1", retryAttempt=0),
    )

    assert message.source == "client:cli"
    assert message.target == "resource:web_bridge"
    assert message.acp["method"] == "_mustang.resource/web_bridge.fetch_tab"
    assert "contract" not in message.model_dump(by_alias=True)


def test_bus_message_rejects_transport_contract_shape() -> None:
    with pytest.raises(ValidationError):
        BusMessage(
            source="client:cli",
            target="agent:primary",
            contract="session/prompt",
            acp={"jsonrpc": "2.0", "method": "session/prompt"},
        )


def test_service_kind_validates_address_prefix() -> None:
    assert service_kind("agent:primary") == "agent"
    assert service_kind("resource:web_bridge") == "resource"
    with pytest.raises(ValueError):
        service_kind("primary")
