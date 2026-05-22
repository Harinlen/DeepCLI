"""ResourceStore-backed global skill declaration index.

The skill body and supporting files stay on disk.  This module persists only
the declaration snapshot needed to make global/user skill discovery durable
and revisioned through ResourceStore.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from kernel.agents.mustang.skills.loader import _discover_layer
from kernel.agents.mustang.skills.types import (
    LoadedSkill,
    SkillFallbackFor,
    SkillManifest,
    SkillRequires,
    SkillSetup,
    SkillSetupEnvVar,
    SkillSource,
)
from kernel.core.config.sqlite_backend import ConfigSQLiteBackend
from kernel.core.storage import ResourceStore

SKILL_DECLARATIONS_FILE = "skills"
SKILL_DECLARATIONS_SECTION = "global_declarations"
SKILL_DECLARATIONS_RESOURCE_KEY = "config.global._.skills.global_declarations"
LEGACY_SKILL_SOURCE_ID = "legacy:skills.user_manifest"


@dataclass(frozen=True, slots=True)
class SkillDeclarationRecord:
    """ResourceStore-backed global skill declaration snapshot."""

    skills: tuple[LoadedSkill, ...]
    revision: int
    payload_hash: str


@dataclass(frozen=True, slots=True)
class SkillDeclarationImportReport:
    """Legacy filesystem-to-ResourceStore import outcome."""

    imported: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    drift: tuple[str, ...] = ()
    target_resource_keys: tuple[str, ...] = ()
    dry_run: bool = False


class SkillDeclarationStore:
    """Skill-owned facade over ResourceStore global skill declarations."""

    def __init__(self, store: ResourceStore) -> None:
        self._store = store
        self._backend = ConfigSQLiteBackend(store)

    @classmethod
    def open(cls, home: Path) -> "SkillDeclarationStore":
        return cls(ResourceStore.open(home))

    def read_global(self) -> SkillDeclarationRecord | None:
        row = self._backend.read(
            file=SKILL_DECLARATIONS_FILE,
            section=SKILL_DECLARATIONS_SECTION,
        )
        if row is None:
            return None
        return SkillDeclarationRecord(
            skills=tuple(_skill_from_payload(item) for item in row.payload.get("skills", [])),
            revision=row.revision,
            payload_hash=row.payload_hash,
        )

    def write_global(
        self,
        skills: list[LoadedSkill] | tuple[LoadedSkill, ...],
        *,
        expected_revision: int | None,
        actor: str | None = None,
    ) -> SkillDeclarationRecord:
        payload = {"skills": [_skill_to_payload(skill) for skill in skills]}
        row = self._backend.write(
            file=SKILL_DECLARATIONS_FILE,
            section=SKILL_DECLARATIONS_SECTION,
            payload=payload,
            expected_revision=expected_revision,
            actor=actor,
        )
        return SkillDeclarationRecord(
            skills=tuple(_skill_from_payload(item) for item in row.payload.get("skills", [])),
            revision=row.revision,
            payload_hash=row.payload_hash,
        )

    def import_legacy_user_skills(
        self,
        user_skills_dir: Path,
        *,
        actor: str = "system",
        dry_run: bool = False,
    ) -> SkillDeclarationImportReport:
        """Import user/global filesystem skills once; report later drift only."""
        discovered = _discover_layer(user_skills_dir, SkillSource.USER, priority=2)
        source_hash = _hash_skills(discovered)
        marker = self._read_marker(LEGACY_SKILL_SOURCE_ID)
        if marker is not None:
            if marker["source_hash"] == source_hash:
                return SkillDeclarationImportReport(
                    skipped=(LEGACY_SKILL_SOURCE_ID,),
                    target_resource_keys=(SKILL_DECLARATIONS_RESOURCE_KEY,),
                    dry_run=dry_run,
                )
            return SkillDeclarationImportReport(
                drift=(LEGACY_SKILL_SOURCE_ID,),
                target_resource_keys=(SKILL_DECLARATIONS_RESOURCE_KEY,),
                dry_run=dry_run,
            )

        if not dry_run:
            self.write_global(discovered, expected_revision=None, actor=actor)
            self._write_marker(
                source_id=LEGACY_SKILL_SOURCE_ID,
                source_path=user_skills_dir,
                source_hash=source_hash,
                source_kind="skills",
                imported_by=actor,
                target_resource_keys=(SKILL_DECLARATIONS_RESOURCE_KEY,),
            )
        return SkillDeclarationImportReport(
            imported=(LEGACY_SKILL_SOURCE_ID,),
            target_resource_keys=(SKILL_DECLARATIONS_RESOURCE_KEY,),
            dry_run=dry_run,
        )

    def close(self) -> None:
        self._store.close()

    def _read_marker(self, source_id: str) -> dict[str, Any] | None:
        row = self._store.read_tx(
            lambda conn: conn.execute(
                "SELECT source_id, source_hash FROM migration_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        )
        return {str(key): value for key, value in row.items()} if row is not None else None

    def _write_marker(
        self,
        *,
        source_id: str,
        source_path: Path,
        source_hash: str,
        source_kind: str,
        imported_by: str,
        target_resource_keys: tuple[str, ...],
    ) -> None:
        self._store.write_tx(
            lambda conn: conn.execute(
                """
                INSERT INTO migration_sources (
                    source_id, source_path, source_hash, source_kind, imported_at,
                    imported_by, target_resource_keys_json, report_json
                )
                VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?)
                """,
                (
                    source_id,
                    str(source_path),
                    source_hash,
                    source_kind,
                    imported_by,
                    orjson.dumps(target_resource_keys).decode(),
                    "{}",
                ),
            )
        )


def _skill_to_payload(skill: LoadedSkill) -> dict[str, Any]:
    manifest = skill.manifest
    description = manifest.description if manifest.has_user_specified_description else manifest.name
    return {
        "manifest": {
            "name": manifest.name,
            "description": description,
            "has_user_specified_description": manifest.has_user_specified_description,
            "allowed_tools": list(manifest.allowed_tools),
            "argument_hint": manifest.argument_hint,
            "argument_names": list(manifest.argument_names),
            "when_to_use": manifest.when_to_use,
            "user_invocable": manifest.user_invocable,
            "disable_model_invocation": manifest.disable_model_invocation,
            "requires": {
                "bins": list(manifest.requires.bins),
                "env": list(manifest.requires.env),
                "tools": list(manifest.requires.tools),
                "toolsets": list(manifest.requires.toolsets),
            },
            "fallback_for": _fallback_to_payload(manifest.fallback_for),
            "os": list(manifest.os),
            "context": manifest.context,
            "agent": manifest.agent,
            "model": manifest.model,
            "hooks": manifest.hooks,
            "paths": list(manifest.paths) if manifest.paths else None,
            "setup": _setup_to_payload(manifest.setup),
            "config": manifest.config,
            "base_dir": str(manifest.base_dir),
            "supporting_files": list(manifest.supporting_files),
        },
        "source": skill.source.value,
        "layer_priority": skill.layer_priority,
        "file_path": str(skill.file_path),
    }


def _skill_from_payload(payload: dict[str, Any]) -> LoadedSkill:
    raw = payload["manifest"]
    manifest = SkillManifest(
        name=str(raw["name"]),
        description=str(raw["description"]),
        has_user_specified_description=bool(raw.get("has_user_specified_description", True)),
        allowed_tools=tuple(raw.get("allowed_tools") or ()),
        argument_hint=raw.get("argument_hint"),
        argument_names=tuple(raw.get("argument_names") or ()),
        when_to_use=raw.get("when_to_use"),
        user_invocable=bool(raw.get("user_invocable", True)),
        disable_model_invocation=bool(raw.get("disable_model_invocation", False)),
        requires=_requires_from_payload(raw.get("requires") or {}),
        fallback_for=_fallback_from_payload(raw.get("fallback_for")),
        os=tuple(raw.get("os") or ()),
        context=raw.get("context"),
        agent=raw.get("agent"),
        model=raw.get("model"),
        hooks=raw.get("hooks"),
        paths=tuple(raw["paths"]) if raw.get("paths") else None,
        setup=_setup_from_payload(raw.get("setup")),
        config=raw.get("config"),
        base_dir=Path(raw["base_dir"]),
        supporting_files=tuple(raw.get("supporting_files") or ()),
    )
    return LoadedSkill(
        manifest=manifest,
        source=SkillSource(str(payload.get("source", SkillSource.USER.value))),
        layer_priority=int(payload.get("layer_priority", 2)),
        file_path=Path(payload["file_path"]),
    )


def _fallback_to_payload(fallback: SkillFallbackFor | None) -> dict[str, Any] | None:
    if fallback is None:
        return None
    return {"tools": list(fallback.tools), "toolsets": list(fallback.toolsets)}


def _fallback_from_payload(payload: dict[str, Any] | None) -> SkillFallbackFor | None:
    if not payload:
        return None
    return SkillFallbackFor(
        tools=tuple(payload.get("tools") or ()),
        toolsets=tuple(payload.get("toolsets") or ()),
    )


def _requires_from_payload(payload: dict[str, Any]) -> SkillRequires:
    return SkillRequires(
        bins=tuple(payload.get("bins") or ()),
        env=tuple(payload.get("env") or ()),
        tools=tuple(payload.get("tools") or ()),
        toolsets=tuple(payload.get("toolsets") or ()),
    )


def _setup_to_payload(setup: SkillSetup | None) -> dict[str, Any] | None:
    if setup is None:
        return None
    return {
        "env": [
            {
                "name": entry.name,
                "prompt": entry.prompt,
                "help": entry.help,
                "secret": entry.secret,
                "optional": entry.optional,
                "default": None if entry.secret else entry.default,
            }
            for entry in setup.env
        ]
    }


def _setup_from_payload(payload: dict[str, Any] | None) -> SkillSetup | None:
    if not payload:
        return None
    env = tuple(
        SkillSetupEnvVar(
            name=str(entry["name"]),
            prompt=str(entry.get("prompt", f"Enter {entry['name']}")),
            help=entry.get("help"),
            secret=bool(entry.get("secret", False)),
            optional=bool(entry.get("optional", False)),
            default=entry.get("default"),
        )
        for entry in payload.get("env", [])
    )
    return SkillSetup(env=env) if env else None


def _hash_skills(skills: list[LoadedSkill]) -> str:
    payload = [_skill_to_payload(skill) for skill in sorted(skills, key=lambda item: item.file_path)]
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()
