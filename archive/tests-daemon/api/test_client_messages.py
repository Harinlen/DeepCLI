"""Tests for the inbound WebSocket message discriminated union."""

from __future__ import annotations

import pytest

from daemon.api.client_messages import (
    Clear,
    DeleteSession,
    ModelSwitch,
    PermissionModeRequest,
    PermissionResponseMsg,
    PlanModeRequest,
    UserMessage,
    ValidationError,
    parse_client_message,
)


class TestParseDispatch:
    """``parse_client_message`` routes by the ``type`` discriminator."""

    def test_user_message(self) -> None:
        msg = parse_client_message({"type": "user_message", "content": "hi"})
        assert isinstance(msg, UserMessage)
        assert msg.content == "hi"

    def test_no_arg_types(self) -> None:
        """Messages with no fields still parse from just ``{type: …}``."""
        assert isinstance(parse_client_message({"type": "clear"}), Clear)

    def test_model_switch_requires_provider_name(self) -> None:
        msg = parse_client_message({"type": "model_switch", "provider_name": "local"})
        assert isinstance(msg, ModelSwitch)
        assert msg.provider_name == "local"

    def test_delete_session_carries_id(self) -> None:
        msg = parse_client_message({"type": "delete_session", "session_id": "abc"})
        assert isinstance(msg, DeleteSession)
        assert msg.session_id == "abc"

    def test_plan_mode_request_action(self) -> None:
        msg = parse_client_message({"type": "plan_mode_request", "action": "enter"})
        assert isinstance(msg, PlanModeRequest)
        assert msg.action == "enter"

    def test_plan_mode_request_rejects_bad_action(self) -> None:
        with pytest.raises(ValidationError):
            parse_client_message({"type": "plan_mode_request", "action": "bogus"})

    def test_permission_mode_request(self) -> None:
        msg = parse_client_message({"type": "permission_mode_request", "action": "plan"})
        assert isinstance(msg, PermissionModeRequest)
        assert msg.action == "plan"

    def test_permission_mode_request_rejects_bad_action(self) -> None:
        with pytest.raises(ValidationError):
            parse_client_message({"type": "permission_mode_request", "action": "nope"})

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            parse_client_message({"type": "does-not-exist"})

    def test_missing_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            parse_client_message({"content": "hi"})


class TestPermissionResponseBackCompat:
    """Legacy ``allowed`` boolean must still parse into a decision."""

    def test_new_three_way_decision(self) -> None:
        msg = parse_client_message(
            {"type": "permission_response", "request_id": "r1", "decision": "always_allow"}
        )
        assert isinstance(msg, PermissionResponseMsg)
        assert msg.decision == "always_allow"

    def test_legacy_allowed_true(self) -> None:
        msg = parse_client_message(
            {"type": "permission_response", "request_id": "r1", "allowed": True}
        )
        assert isinstance(msg, PermissionResponseMsg)
        assert msg.decision == "allow"

    def test_legacy_allowed_false(self) -> None:
        msg = parse_client_message(
            {"type": "permission_response", "request_id": "r1", "allowed": False}
        )
        assert msg.decision == "deny"

    def test_explicit_decision_wins_over_legacy(self) -> None:
        """If both fields are present, ``decision`` takes precedence."""
        msg = parse_client_message(
            {
                "type": "permission_response",
                "request_id": "r1",
                "allowed": True,
                "decision": "deny",
            }
        )
        assert msg.decision == "deny"

    def test_decision_defaults_to_deny_when_both_missing(self) -> None:
        msg = parse_client_message({"type": "permission_response", "request_id": "r1"})
        assert msg.decision == "deny"

    def test_bad_decision_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_client_message(
                {"type": "permission_response", "request_id": "r1", "decision": "maybe"}
            )
