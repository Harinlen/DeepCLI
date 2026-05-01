"""Permission option projection for tool approval prompts."""

from __future__ import annotations

from kernel.orchestrator.permissions import PermissionRequestOption
from kernel.tool_authz import PermissionSuggestionBtn


def permission_options_from_suggestions(
    suggestions: list[PermissionSuggestionBtn],
) -> tuple[PermissionRequestOption, ...]:
    """Project authorizer suggestions into session permission options.

    Args:
        suggestions: Buttons offered by ToolAuthorizer for this ask.

    Returns:
        Permission options that Session can map onto ACP.
    """
    options: list[PermissionRequestOption] = []
    for suggestion in suggestions:
        outcome = getattr(suggestion, "outcome", None)
        label = str(getattr(suggestion, "label", "") or "")
        if outcome == "allow_once":
            options.append(
                PermissionRequestOption(
                    option_id="allow_once",
                    name=label or "Allow once",
                    kind="allow_once",
                )
            )
        elif outcome == "allow_always":
            options.append(
                PermissionRequestOption(
                    option_id="allow_always",
                    name=label or "Allow always",
                    kind="allow_always",
                )
            )
        elif outcome == "deny":
            options.append(
                PermissionRequestOption(
                    option_id="reject",
                    name=label or "Reject",
                    kind="reject_once",
                )
            )
    return tuple(options)
