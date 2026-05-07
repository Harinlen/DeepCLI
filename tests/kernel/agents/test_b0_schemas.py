from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from kernel.agents import (
    AgentRegistrationRequest,
    AgentRuntimeKind,
    CallerIdentity,
    CallerIdentityKind,
    HubFrame,
    HubFrameType,
    ManagementCall,
    ManagementCapability,
    RegistrationToken,
    RouterFrame,
    RouterFrameKind,
    RouterTarget,
    default_primary_agent_definition,
)


def test_default_primary_agent_definition_uses_seed_state_path(
    tmp_path: Path,
) -> None:
    definition = default_primary_agent_definition(
        home=tmp_path,
        workspace=tmp_path / "workspace",
    )

    assert definition.id == "primary"
    assert definition.bindings.native_default is True
    assert definition.state_dir == str(tmp_path / "agents" / "primary")
    assert definition.session_store_path == str(
        tmp_path / "agents" / "primary" / "sessions" / "sessions.db"
    )
    assert ManagementCapability.AGENT_CREATE in definition.policy.management_capabilities


def test_agent_definition_rejects_runtime_only_state(tmp_path: Path) -> None:
    definition = default_primary_agent_definition(
        home=tmp_path,
        workspace=tmp_path / "workspace",
    )
    payload = definition.model_dump()
    payload["process_id"] = 123

    with pytest.raises(ValidationError):
        type(definition).model_validate(payload)


def test_auth_identity_registration_token_and_capability_are_not_interchangeable(
) -> None:
    access_identity = CallerIdentity(
        kind=CallerIdentityKind.ACCESS,
        subject_id="cli:test",
        connection_id="conn-1",
    )
    token = RegistrationToken(
        token_id="token-1",
        secret="secret",
        issued_to_agent_id="primary",
    )

    with pytest.raises(ValidationError):
        AgentRegistrationRequest.model_validate(
            {
                "agent_id": "primary",
                "runtime_kind": AgentRuntimeKind.in_process_session_agent,
                "websocket_endpoint": "ws://127.0.0.1:10000/agent",
                "registration_token": access_identity.model_dump(),
            }
        )

    with pytest.raises(ValidationError):
        ManagementCall.model_validate(
            {
                "operation": "status",
                "caller": token.model_dump(),
                "capability": ManagementCapability.AGENT_STATUS,
            }
        )

    with pytest.raises(ValidationError):
        ManagementCall.model_validate(
            {
                "operation": "status",
                "caller": access_identity.model_dump(),
                "capability": token.model_dump(),
            }
        )


def test_router_frame_has_message_plane_kinds_only() -> None:
    frame = RouterFrame(
        frame_id="frame-1",
        kind=RouterFrameKind.USER_MESSAGE,
        source="access:native",
        target=RouterTarget(agent_id="primary"),
        caller=CallerIdentity(
            kind=CallerIdentityKind.ACCESS,
            subject_id="cli:test",
        ),
        payload={"text": "hello"},
    )

    assert frame.kind == RouterFrameKind.USER_MESSAGE
    assert "create" not in {kind.value for kind in RouterFrameKind}
    assert "delete" not in {kind.value for kind in RouterFrameKind}
    assert "status" not in {kind.value for kind in RouterFrameKind}


def test_hub_frame_encode_decode_contract_without_fastapi() -> None:
    frame = HubFrame(
        frame_id="hub-1",
        frame_type=HubFrameType.REQUEST,
        contract="router.frame",
        payload={"frame_id": "frame-1"},
    )

    decoded = HubFrame.from_json_bytes(frame.to_json_bytes())

    assert decoded == frame
    assert "fastapi" not in HubFrame.__module__
