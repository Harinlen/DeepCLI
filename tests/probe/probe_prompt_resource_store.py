"""Closure probe for ResourceStore-backed global prompt declarations."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import orjson

from kernel.agents.mustang.prompts import PromptManager
from kernel.agents.mustang.prompts.declarations import PromptDeclarationStore
from kernel.core.storage import ResourceStore


PROMPT_BODY_PLAINTEXT = "prompt-body-plaintext-must-not-enter-sqlite"
RENDERED_PLAINTEXT = "rendered-prompt-secret-must-not-enter-sqlite"


def _write_prompt(root: Path, key: str, text: str) -> None:
    path = root / f"{key}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run() -> dict[str, object]:
    with TemporaryDirectory(prefix="mustang-prompt-rs-") as tmp:
        home = Path(tmp)
        defaults = home / "defaults"
        global_prompts = home / "prompts"
        project_prompts = home / "project" / ".mustang" / "prompts"
        _write_prompt(defaults, "orchestrator/system", "default system")
        _write_prompt(global_prompts, "orchestrator/system", f"{PROMPT_BODY_PLAINTEXT} {{name}}")

        first = PromptManager(
            defaults_dir=defaults,
            user_dirs=[global_prompts],
            resource_home=home,
        )
        first.load()
        prompt_startup_from_resource_store = (
            first.declaration_record is not None
            and first.declaration_record.prompts[0]["key"] == "orchestrator/system"
        )
        legacy_import_once = (
            first.declaration_import_report is not None
            and first.declaration_import_report.imported == ("legacy:prompts.user_manifest",)
        )
        revision_after_import = first.declaration_record.revision if first.declaration_record else 0
        rendered = first.render("orchestrator/system", name=RENDERED_PLAINTEXT)

        _write_prompt(
            global_prompts,
            "orchestrator/system",
            f"drifted prompt body {{name}} {{extra}} {PROMPT_BODY_PLAINTEXT}",
        )
        second = PromptManager(
            defaults_dir=defaults,
            user_dirs=[global_prompts],
            resource_home=home,
        )
        second.load()
        legacy_drift_ignored = (
            second.declaration_import_report is not None
            and second.declaration_import_report.drift == ("legacy:prompts.user_manifest",)
            and second.declaration_record is not None
            and second.declaration_record.prompts[0]["placeholders"] == ["name"]
            and "extra" not in second.declaration_record.prompts[0]["placeholders"]
        )

        declarations = PromptDeclarationStore.open(home)
        try:
            current = declarations.read_global()
            revision_after_add = declarations.write_global(
                list(current.prompts if current else ())
                + [{"key": "custom/a", "enabled": True, "source": "global_user"}],
                expected_revision=current.revision if current else None,
                actor="probe",
            ).revision
            revision_after_update = declarations.write_global(
                [
                    {"key": "orchestrator/system", "enabled": False, "source": "global_user"},
                    {"key": "custom/a", "enabled": True, "source": "global_user"},
                ],
                expected_revision=revision_after_add,
                actor="probe",
            ).revision
            revision_after_delete = declarations.write_global(
                [{"key": "custom/a", "enabled": True, "source": "global_user"}],
                expected_revision=revision_after_update,
                actor="probe",
            ).revision
        finally:
            declarations.close()

        reload_manager = PromptManager(defaults_dir=defaults, user_dirs=[global_prompts], resource_home=home)
        reload_manager.load()
        prompt_reload_sees_resource_store_update = (
            reload_manager.declaration_record is not None
            and reload_manager.declaration_record.revision == revision_after_delete
            and reload_manager.declaration_record.prompts[0]["key"] == "custom/a"
        )

        _write_prompt(project_prompts, "orchestrator/system", "project override")
        project_manager = PromptManager(
            defaults_dir=defaults,
            user_dirs=[global_prompts, project_prompts],
            resource_home=home,
        )
        project_manager.load()
        project_override_wins = project_manager.get("orchestrator/system") == "project override"

        store = ResourceStore.open(home)
        try:
            payload = store.read_tx(
                lambda conn: conn.execute(
                    "SELECT payload_json FROM config_sections "
                    "WHERE file = 'prompts' AND section = 'global_declarations'"
                ).fetchone()[0]
            )
            export_path = home / "export.json"
            store.export("json", export_path, dry_run=False)
            exported = orjson.dumps(orjson.loads(export_path.read_bytes())).decode()
        finally:
            store.close()

        prompt_body_persisted_as_declaration = (
            PROMPT_BODY_PLAINTEXT in payload or PROMPT_BODY_PLAINTEXT in exported
        )
        rendered_prompt_persisted = RENDERED_PLAINTEXT in payload or RENDERED_PLAINTEXT in exported

        return {
            "probe": "prompt_resource_store",
            "prompt_startup_from_resource_store": prompt_startup_from_resource_store,
            "legacy_import_once": legacy_import_once,
            "legacy_drift_ignored": legacy_drift_ignored,
            "revision_after_import": revision_after_import,
            "revision_after_add": revision_after_add,
            "revision_after_update": revision_after_update,
            "revision_after_delete": revision_after_delete,
            "prompt_reload_sees_resource_store_update": prompt_reload_sees_resource_store_update,
            "project_override_wins": project_override_wins,
            "rendered_prompt_contains_runtime_secret": RENDERED_PLAINTEXT in rendered,
            "prompt_body_persisted_as_declaration": prompt_body_persisted_as_declaration,
            "rendered_prompt_persisted": rendered_prompt_persisted,
            "result": "PASS",
        }


def main() -> None:
    result = _run()
    false_failures = [
        key
        for key, value in result.items()
        if isinstance(value, bool)
        and key not in {"prompt_body_persisted_as_declaration", "rendered_prompt_persisted"}
        and not value
    ]
    true_failures = [
        key
        for key in ("prompt_body_persisted_as_declaration", "rendered_prompt_persisted")
        if result[key]
    ]
    failures = false_failures + true_failures
    if failures:
        result["result"] = "FAIL"
    for key, value in result.items():
        print(f"{key}={value}")
    if failures:
        raise SystemExit(f"failed checks: {', '.join(failures)}")


if __name__ == "__main__":
    main()
