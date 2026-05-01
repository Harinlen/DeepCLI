"""ConfigManager — bootstrap service for layered runtime config.

ConfigManager is constructed and started by the kernel lifespan right
after :class:`kernel.flags.FlagManager`.  Every regular subsystem
depends on it being up, so a failure during :meth:`startup` is fatal
to kernel boot (handled by the lifespan, not here).

Public surface — only three methods matter:

- :meth:`startup` — scan global / project config dirs, fold in CLI
  overrides, build an in-memory ``{file: raw_dict}`` map.
- :meth:`bind_section` — called by the owning subsystem to claim a
  section; returns a :class:`MutableSection` that can ``update`` and
  persist.  First bind wins; a second bind of the same
  ``(file, section)`` raises ``ValueError``.
- :meth:`get_section` — called by any reader to obtain a
  :class:`ReadOnlySection`.  Readers may call this **before** the
  owner binds; the section is materialized on first touch.

There is no ``shutdown``: section updates persist synchronously, so
nothing is left to drain at kernel exit.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from kernel.config import loader
from kernel.config.section import (
    MutableSection,
    ReadOnlySection,
    _Section,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_DEFAULT_GLOBAL_DIR = Path.home() / ".mustang" / "config"
_DEFAULT_PROJECT_SUBDIR = Path(".mustang") / "config"


class ConfigManager:
    """Layered runtime config store with owner/reader separation.

    Parameters
    ----------
    global_dir:
        Override the global user config directory.  Defaults to
        ``~/.mustang/config``.  Tests pass a ``tmp_path``-based dir
        to stay hermetic.
    project_dir:
        Override the project-layer directory.  Defaults to
        ``<cwd>/.mustang/config``.  Passed separately (not derived
        inside) so tests never have to ``monkeypatch`` ``os.getcwd``.
    cli_overrides:
        Iterable of ``<file>.<section>.<key>=<value>`` strings
        collected from the kernel CLI.  Currently wired to an empty
        tuple in ``app.py``; this parameter exists so ``__main__``
        can inject parsed ``--config`` flags later without reshaping
        the class.
    """

    def __init__(
        self,
        *,
        global_dir: Path | None = None,
        project_dir: Path | None = None,
        cli_overrides: Sequence[str] | None = None,
        secret_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._global_dir: Path = global_dir if global_dir is not None else _DEFAULT_GLOBAL_DIR
        self._project_dir: Path = (
            project_dir if project_dir is not None else Path.cwd() / _DEFAULT_PROJECT_SUBDIR
        )
        self._cli_overrides: tuple[str, ...] = tuple(cli_overrides or ())
        self._secret_resolver = secret_resolver

        # Populated by ``startup``.
        self._raw: dict[str, dict[str, Any]] = {}
        # (file, section) → _Section — created lazily by first
        # bind/get call.
        self._sections: dict[tuple[str, str], _Section[Any]] = {}
        # (file, section) pairs that have already been claimed by an
        # owner; enforces the first-bind-wins invariant.
        self._owned: set[tuple[str, str]] = set()
        self._started = False

    async def startup(self) -> None:
        """Scan the three layers and fold env / CLI overrides in.

        Safe to call more than once — subsequent calls are no-ops.
        Raises :class:`ValueError` if any YAML file is malformed;
        the lifespan treats that as fatal to kernel boot.
        """
        if self._started:
            return
        self._raw = loader.collect(
            global_dir=self._global_dir,
            project_dir=self._project_dir,
            cli_overrides=self._cli_overrides,
        )
        self._started = True
        logger.info(
            "ConfigManager loaded %d file(s) from global=%s project=%s",
            len(self._raw),
            self._global_dir,
            self._project_dir,
        )

    def bind_section(
        self,
        *,
        file: str,
        section: str,
        schema: type[T],
    ) -> MutableSection[T]:
        """Claim a section as the single writer.

        The first call for a ``(file, section)`` pair wins — a later
        call (even with the same schema) raises :class:`ValueError`.
        If a reader has already materialized the section via
        :meth:`get_section`, this call validates the schema matches
        the one the reader used; mismatches raise :class:`ValueError`.

        Parameters
        ----------
        file:
            Short file name (no extension).  Decides which
            ``<file>.yaml`` the section writes back to.  Subsystems
            choose freely: share ``config.yaml`` or carve out
            ``mcp.yaml`` for isolation.
        section:
            Top-level key inside ``<file>.yaml``.  Must be unique
            per file.
        schema:
            Pydantic model used to validate the raw dict and to
            round-trip future updates.

        Returns
        -------
        MutableSection[T]
            Owner wrapper with ``get`` / ``update`` / ``changed``.

        Raises
        ------
        ValueError
            If the section is already bound, or if a reader bound it
            first with a different schema.
        pydantic.ValidationError
            If the raw YAML data fails schema validation.
        """
        key = (file, section)
        if key in self._owned:
            raise ValueError(f"Section already bound by an earlier owner: {file}.{section}")
        sec = self._get_or_create_section(file, section, schema)
        self._owned.add(key)
        return MutableSection(sec)

    def get_section(
        self,
        *,
        file: str,
        section: str,
        schema: type[T],
    ) -> ReadOnlySection[T]:
        """Obtain a read-only view of a section.

        Callable any number of times, and may run **before** the
        owner's :meth:`bind_section`: the section is materialized on
        first touch and subsequent binds/gets reuse the same
        underlying state.

        The ``schema`` passed in must match the one already associated
        with the section (from whichever earlier call created it).
        Using a different schema raises :class:`ValueError` because
        validation state would diverge.

        Raises
        ------
        ValueError
            If a previous call used a different schema class.
        pydantic.ValidationError
            If the raw YAML data fails schema validation on first
            materialization.
        """
        sec = self._get_or_create_section(file, section, schema)
        return ReadOnlySection(sec)

    def _get_or_create_section(
        self,
        file: str,
        section: str,
        schema: type[T],
    ) -> _Section[T]:
        """Lookup or materialize the shared ``_Section`` state.

        First caller for a ``(file, section)`` pair fixes the schema
        identity; later callers must pass the same class object.
        Comparing with ``is`` is intentional: two structurally-equal
        schemas defined in different modules would still validate
        successfully but might carry different defaults or custom
        validators, so we refuse silent confusion.
        """
        key = (file, section)
        existing = self._sections.get(key)
        if existing is not None:
            if existing.schema is not schema:
                raise ValueError(
                    f"Schema mismatch for {file}.{section}: already "
                    f"registered as {existing.schema.__name__}, got "
                    f"{schema.__name__}"
                )
            return existing  # type: ignore[return-value]

        raw_section = self._raw.get(file, {}).get(section) or {}
        if not isinstance(raw_section, dict):
            raise ValueError(
                f"Config {file}.{section} must be a mapping, got {type(raw_section).__name__}"
            )

        # Expand ${secret:name} references before Pydantic validation.
        if self._secret_resolver is not None:
            raw_section = _expand_secrets_in_dict(raw_section, self._secret_resolver)

        instance = schema.model_validate(raw_section)
        sec: _Section[T] = _Section(
            file=file,
            section=section,
            schema=schema,
            current=instance,
            write_path=self._global_dir / f"{file}.yaml",
        )
        self._sections[key] = sec
        return sec


# ---------------------------------------------------------------------------
# ${secret:name} expansion helpers
# ---------------------------------------------------------------------------

_SECRET_RE = re.compile(r"\$\{secret:([^}]+)\}")


def _expand_secrets_in_dict(
    data: dict[str, Any],
    resolver: Callable[[str], str | None],
) -> dict[str, Any]:
    """Recursively expand ``${secret:name}`` in leaf string values.

    The *resolver* takes a secret **name** (not a template) and returns
    the plaintext value, or ``None`` if not found.
    """
    out: dict[str, Any] = {}
    for k, v in data.items():
        out[k] = _expand_in_value(v, resolver)
    return out


def _expand_in_value(value: Any, resolver: Callable[[str], str | None]) -> Any:
    """Expand ``${secret:name}`` in a single value (recursive)."""
    if isinstance(value, str):

        def _replace(m: re.Match[str]) -> str:
            name = m.group(1)
            resolved = resolver(name)
            if resolved is None:
                from kernel.secrets.types import SecretNotFoundError

                raise SecretNotFoundError(
                    f"Secret {name!r} referenced in config but not found. "
                    f"Use '/auth set {name} <value>' to store it."
                )
            return resolved

        return _SECRET_RE.sub(_replace, value)
    if isinstance(value, dict):
        return _expand_secrets_in_dict(value, resolver)
    if isinstance(value, list):
        return [_expand_in_value(item, resolver) for item in value]
    return value
