"""RuleStore — layered ``PermissionRule`` snapshot with hot reload.

Owns the in-memory rule table consumed by :class:`RuleEngine`.  Two
source layers feed it:

- **Config layer** — a ``PermissionsSection`` bound to
  ``ConfigManager``.  Updates to the yaml file fire a ``changed``
  signal; ``RuleStore`` subscribes and re-parses into a new rule table.
- **Flag layer** — process-start-time list populated from CLI / env.
  Runtime-frozen (aligned with the FlagManager contract).

Parse failures keep the old table alive so a malformed yaml does not
brick authorization — ``log + fire rule_parse_failed`` instead (hook
wiring lives in the Subsystem, not here).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from kernel.tool_authz.rule_parser import parse_rule
from kernel.tool_authz.types import PermissionRule, RuleSource

if TYPE_CHECKING:
    from kernel.config.section import MutableSection
    from kernel.tool_authz.config_section import PermissionsSection

logger = logging.getLogger(__name__)


class RuleStore:
    """Holds the current ``list[PermissionRule]`` for RuleEngine.

    The store presents a single flat list sorted in precedence order:
    later entries override earlier ones (matches Claude Code's
    source-ordered iteration in ``permissionsLoader.ts``).
    """

    def __init__(self) -> None:
        self._config_rules: list[PermissionRule] = []
        self._flag_rules: list[PermissionRule] = []

    # ------------------------------------------------------------------
    # Config layer
    # ------------------------------------------------------------------

    def bind_config(self, section: MutableSection[PermissionsSection]) -> None:
        """Parse the current config + subscribe to updates."""
        current = section.get()
        self._config_rules = _parse_section(current)

        async def _on_change(_old: PermissionsSection, new: PermissionsSection) -> None:
            try:
                parsed = _parse_section(new)
            except Exception:
                # Keep the old rules, log + continue.  Malformed YAML
                # must not brick authorization.
                logger.exception(
                    "RuleStore: re-parse after config change failed — keeping old rules"
                )
                return
            self._config_rules = parsed
            logger.info("RuleStore: reloaded %d config rules", len(parsed))

        section.changed.connect(_on_change)

    # ------------------------------------------------------------------
    # Flag layer
    # ------------------------------------------------------------------

    def load_flag_layer(self, allow: list[str], deny: list[str], ask: list[str]) -> None:
        """Populate the flag layer from CLI / env strings.

        Called once at startup; flag rules are runtime-frozen.
        """
        self._flag_rules = _build_rules(allow=allow, deny=deny, ask=ask, source=RuleSource.FLAG)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> list[PermissionRule]:
        """Return the full rule list in precedence order (later = higher)."""
        # Config layer first, then flag layer — matches the doc's
        # user → project → local → flag precedence.  Since ConfigManager
        # has already merged user/project/local into one section, we
        # represent them together as USER-sourced rules.
        return [*self._config_rules, *self._flag_rules]


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _parse_section(section: PermissionsSection) -> list[PermissionRule]:
    """Expand a ``PermissionsSection`` into flat rule list."""
    return _build_rules(
        allow=section.allow,
        deny=section.deny,
        ask=section.ask,
        source=RuleSource.USER,
    )


def _build_rules(
    *,
    allow: list[str],
    deny: list[str],
    ask: list[str],
    source: RuleSource,
) -> list[PermissionRule]:
    """Flatten three lists into ``PermissionRule`` objects.

    Ordering within a source: ``deny`` first, then ``ask``, then
    ``allow`` — so when the engine iterates in insertion order,
    higher-priority behaviours are checked first.  (Engine also does
    ``deny > ask > allow`` arbitration explicitly, so this ordering is
    redundant for correctness but helps debug output read naturally.)
    """
    rules: list[PermissionRule] = []
    idx = 0
    for raw in deny:
        rules.append(parse_rule(raw, "deny", source, idx))
        idx += 1
    for raw in ask:
        rules.append(parse_rule(raw, "ask", source, idx))
        idx += 1
    for raw in allow:
        rules.append(parse_rule(raw, "allow", source, idx))
        idx += 1
    return rules


__all__ = ["RuleStore"]
