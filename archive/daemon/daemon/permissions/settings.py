"""Persistence layer for user-level permission rules.

Rules are stored in ``~/.mustang/settings.json`` under the
``"permissions"`` key::

    {
      "permissions": {
        "allow": ["Bash(git *)", "file_read", "glob", "grep"],
        "deny": ["Bash(rm -rf *)"]
      },
      ...other top-level settings preserved on round-trip...
    }

The schema is intentionally narrow (no per-project overrides, no
multi-source precedence) — Mustang only needs **global** rules in
Phase 4.  Project-level ``.mustang/settings.json`` is deferred to
Phase 5+.

The class is concurrency-safe for our use-case: the daemon is the
sole writer, so no file lock is needed.  ``add_allow_rule`` /
``add_deny_rule`` persist immediately so a crash between "approve"
and "next query" still retains the rule.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from daemon.permissions.rules import PermissionRule, parse_rule

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS_PATH = Path("~/.mustang/settings.json").expanduser()


class PermissionSettings:
    """Load, mutate, and persist allow/deny rules.

    Holds two lists of :class:`PermissionRule` — ``allow_rules`` and
    ``deny_rules`` — mirroring the on-disk JSON structure.  Other
    top-level keys in ``settings.json`` are preserved on save.

    Args:
        settings_path: Full path to the settings file.  Defaults to
            ``~/.mustang/settings.json``.
    """

    def __init__(self, settings_path: Path | None = None) -> None:
        self._path: Path = settings_path or DEFAULT_SETTINGS_PATH
        self._allow: list[PermissionRule] = []
        self._deny: list[PermissionRule] = []
        # Top-level JSON keys other than "permissions" — preserved
        # verbatim on save so we do not trample unrelated user data.
        self._extra: dict[str, Any] = {}

    @property
    def path(self) -> Path:
        """Path to the backing JSON file."""
        return self._path

    @property
    def allow_rules(self) -> list[PermissionRule]:
        """Snapshot of the current allow-list (order preserved)."""
        return list(self._allow)

    @property
    def deny_rules(self) -> list[PermissionRule]:
        """Snapshot of the current deny-list (order preserved)."""
        return list(self._deny)

    # -- I/O ----------------------------------------------------------

    def load(self) -> None:
        """Read ``settings.json`` from disk.

        Missing file → start with empty rule lists.  Malformed JSON
        or entries are logged and skipped; the loader never raises
        during normal startup.
        """
        self._allow.clear()
        self._deny.clear()
        self._extra.clear()

        if not self._path.exists():
            return

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cannot read %s: %s (starting with empty rules)", self._path, exc)
            return

        if not isinstance(raw, dict):
            logger.warning("%s is not a JSON object; ignoring", self._path)
            return

        perms_obj = raw.pop("permissions", {})
        self._extra = raw

        if not isinstance(perms_obj, dict):
            return

        self._allow = _parse_rule_list(perms_obj.get("allow", []), "allow")
        self._deny = _parse_rule_list(perms_obj.get("deny", []), "deny")

    def save(self) -> None:
        """Write the current rule set back to disk.

        Creates the parent directory if it does not exist.  Preserves
        non-``permissions`` top-level keys loaded earlier.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)

        payload: dict[str, Any] = dict(self._extra)
        payload["permissions"] = {
            "allow": [r.rule_str for r in self._allow],
            "deny": [r.rule_str for r in self._deny],
        }

        self._path.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

    # -- Mutation -----------------------------------------------------

    def add_allow_rule(self, rule_str: str) -> bool:
        """Append a new allow rule and persist.

        Returns:
            ``True`` if the rule was added, ``False`` if an identical
            ``rule_str`` already existed (de-duplication).

        Raises:
            ValueError: If ``rule_str`` is malformed.
        """
        return self._add(rule_str, "allow")

    def add_deny_rule(self, rule_str: str) -> bool:
        """Append a new deny rule and persist.

        Returns:
            ``True`` if the rule was added, ``False`` if a duplicate.

        Raises:
            ValueError: If ``rule_str`` is malformed.
        """
        return self._add(rule_str, "deny")

    def remove_rule(self, rule_str: str) -> bool:
        """Remove the first rule whose text matches *rule_str*.

        Searches both allow- and deny-lists.  Persists on success.

        Returns:
            ``True`` if a matching rule was removed, else ``False``.
        """
        for bucket in (self._allow, self._deny):
            for i, rule in enumerate(bucket):
                if rule.rule_str == rule_str:
                    bucket.pop(i)
                    self.save()
                    return True
        return False

    # -- Internal -----------------------------------------------------

    def _add(self, rule_str: str, effect: str) -> bool:
        # De-duplicate by rule_str within the same bucket.
        bucket = self._allow if effect == "allow" else self._deny
        if any(r.rule_str == rule_str for r in bucket):
            return False

        parsed = parse_rule(rule_str)
        bucket.append(PermissionRule(tool_rule=parsed, effect=effect, rule_str=rule_str))
        self.save()
        return True


def _parse_rule_list(raw: Any, effect: str) -> list[PermissionRule]:
    """Parse a list of rule strings loaded from JSON.

    Malformed entries are logged and skipped — a single bad rule
    must not block startup.
    """
    if not isinstance(raw, list):
        return []

    out: list[PermissionRule] = []
    for item in raw:
        if not isinstance(item, str):
            logger.warning("Skipping non-string %s rule: %r", effect, item)
            continue
        try:
            parsed = parse_rule(item)
        except ValueError as exc:
            logger.warning("Skipping malformed %s rule %r: %s", effect, item, exc)
            continue
        out.append(PermissionRule(tool_rule=parsed, effect=effect, rule_str=item))
    return out
