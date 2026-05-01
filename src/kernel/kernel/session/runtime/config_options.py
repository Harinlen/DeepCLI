"""Session mode/config descriptors exposed through ACP."""

from __future__ import annotations

from typing import Any

from pydantic.alias_generators import to_camel

from kernel.protocol.interfaces.contracts.session_config import (
    ConfigOptionChoice,
    ConfigOptionDescriptor,
    SessionModeInfo,
    SessionModeState,
)

DEFAULT_MODE_ID = "default"
PLAN_MODE_ID = "plan"
BYPASS_MODE_ID = "bypass"
ACCEPT_EDITS_MODE_ID = "accept_edits"
AUTO_MODE_ID = "auto"
DONT_ASK_MODE_ID = "dont_ask"
MODE_CONFIG_ID = "mode"

_MODE_DEFINITIONS = (
    ConfigOptionChoice(
        value=DEFAULT_MODE_ID,
        name="Default",
        description="Normal coding-agent mode.",
    ),
    ConfigOptionChoice(
        value=PLAN_MODE_ID,
        name="Plan",
        description="Planning mode for discussion before implementation.",
    ),
    ConfigOptionChoice(
        value=BYPASS_MODE_ID,
        name="Bypass permissions",
        description="Allow tool calls without prompting.",
    ),
    ConfigOptionChoice(
        value=ACCEPT_EDITS_MODE_ID,
        name="Accept edits",
        description="Auto-allow edit tools while keeping other prompts.",
    ),
    ConfigOptionChoice(
        value=AUTO_MODE_ID,
        name="Auto",
        description="Use automatic permission handling.",
    ),
    ConfigOptionChoice(
        value=DONT_ASK_MODE_ID,
        name="Don't ask",
        description="Run only pre-approved tools and deny prompts.",
    ),
)

_VALID_MODE_IDS = frozenset(choice.value for choice in _MODE_DEFINITIONS)
_MODE_CHOICES = _MODE_DEFINITIONS
_AVAILABLE_MODES = tuple(
    SessionModeInfo(id=choice.value, name=choice.name, description=choice.description)
    for choice in _MODE_DEFINITIONS
)


def normalise_mode_id(mode_id: str | None) -> str:
    """Collapse the internal ``None`` mode into ACP's explicit default id."""
    return mode_id or DEFAULT_MODE_ID


def validate_mode_id(mode_id: str) -> str:
    """Return ``mode_id`` if it is supported, otherwise raise ``ValueError``."""
    if mode_id not in _VALID_MODE_IDS:
        raise ValueError(mode_id)
    return mode_id


def mode_state(mode_id: str | None) -> SessionModeState:
    """Build the ACP mode state for a session."""
    return SessionModeState(
        current_mode_id=normalise_mode_id(mode_id),
        available_modes=list(_AVAILABLE_MODES),
    )


def config_descriptors(
    values: dict[str, Any], mode_id: str | None = None
) -> list[ConfigOptionDescriptor]:
    """Build full ACP config option descriptors from current session values."""
    current_mode = str(values.get(MODE_CONFIG_ID) or normalise_mode_id(mode_id))
    return [
        ConfigOptionDescriptor(
            config_id=MODE_CONFIG_ID,
            name="Mode",
            type="select",
            current_value=current_mode,
            options=list(_MODE_CHOICES),
            description="Controls the active session mode.",
        )
    ]


def config_descriptor_dicts(
    values: dict[str, Any], mode_id: str | None = None
) -> list[dict[str, Any]]:
    """Return descriptor dicts for ACP update/replay helpers."""
    return [
        _camelise(item.model_dump(exclude_none=True))
        for item in config_descriptors(values, mode_id)
    ]


def _camelise(value: Any) -> Any:
    if isinstance(value, dict):
        return {to_camel(k): _camelise(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_camelise(item) for item in value]
    return value
