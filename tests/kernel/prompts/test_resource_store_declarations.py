"""ResourceStore-backed global prompt declaration tests."""

from __future__ import annotations

from pathlib import Path

import orjson

from kernel.agents.mustang.prompts import PromptManager
from kernel.agents.mustang.prompts.declarations import PromptDeclarationStore
from kernel.core.storage import ResourceStore


def _write_prompt(root: Path, key: str, text: str) -> None:
    path = root / f"{key}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_prompt_declarations_startup_from_resource_store(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    _write_prompt(defaults, "system", "default")
    declarations = PromptDeclarationStore.open(tmp_path)
    try:
        declarations.write_global(
            [
                {
                    "key": "system",
                    "enabled": True,
                    "source": "global_user",
                    "source_path": str(tmp_path / "prompts" / "system.txt"),
                    "has_placeholders": False,
                    "placeholders": [],
                }
            ],
            expected_revision=None,
            actor="test",
        )
    finally:
        declarations.close()

    manager = PromptManager(defaults_dir=defaults, resource_home=tmp_path)
    manager.load()

    assert manager.declaration_record is not None
    assert manager.declaration_record.prompts[0]["key"] == "system"
    assert manager.declaration_record.revision == 1


def test_legacy_prompt_manifest_import_once_and_drift_ignored(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    _write_prompt(defaults, "system", "default")
    global_prompts = tmp_path / "prompts"
    _write_prompt(global_prompts, "system", "original {name}")

    first = PromptManager(defaults_dir=defaults, user_dirs=[global_prompts], resource_home=tmp_path)
    first.load()
    assert first.declaration_import_report is not None
    assert first.declaration_import_report.imported == ("legacy:prompts.user_manifest",)

    _write_prompt(global_prompts, "system", "drifted {name} {extra}")
    second = PromptManager(defaults_dir=defaults, user_dirs=[global_prompts], resource_home=tmp_path)
    second.load()

    assert second.declaration_import_report is not None
    assert second.declaration_import_report.drift == ("legacy:prompts.user_manifest",)
    assert second.declaration_record is not None
    assert second.declaration_record.prompts[0]["placeholders"] == ["name"]
    assert second.get("system") == "drifted {name} {extra}"


def test_prompt_declaration_revision_bumps_on_add_update_delete(tmp_path: Path) -> None:
    declarations = PromptDeclarationStore.open(tmp_path)
    try:
        one = declarations.write_global(
            [{"key": "a", "enabled": True}],
            expected_revision=None,
            actor="test",
        )
        two = declarations.write_global(
            [{"key": "a", "enabled": True}, {"key": "b", "enabled": True}],
            expected_revision=one.revision,
            actor="test",
        )
        three = declarations.write_global(
            [{"key": "a", "enabled": False}, {"key": "b", "enabled": True}],
            expected_revision=two.revision,
            actor="test",
        )
        four = declarations.write_global(
            [{"key": "b", "enabled": True}],
            expected_revision=three.revision,
            actor="test",
        )

        assert (one.revision, two.revision, three.revision, four.revision) == (1, 2, 3, 4)
    finally:
        declarations.close()


def test_project_prompt_override_still_wins(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults"
    global_prompts = tmp_path / "prompts"
    project_prompts = tmp_path / "project" / ".mustang" / "prompts"
    _write_prompt(defaults, "system", "default")
    _write_prompt(global_prompts, "system", "global")
    _write_prompt(project_prompts, "system", "project")

    manager = PromptManager(
        defaults_dir=defaults,
        user_dirs=[global_prompts, project_prompts],
        resource_home=tmp_path,
    )
    manager.load()

    assert manager.get("system") == "project"


def test_prompt_declarations_do_not_persist_body_or_rendered_text(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults"
    global_prompts = tmp_path / "prompts"
    body_plaintext = "prompt-body-should-not-be-in-resource-store"
    rendered_plaintext = "rendered-secret-should-not-be-in-resource-store"
    _write_prompt(defaults, "system", "default")
    _write_prompt(global_prompts, "system", f"{body_plaintext} {{secret}}")

    manager = PromptManager(defaults_dir=defaults, user_dirs=[global_prompts], resource_home=tmp_path)
    manager.load()
    assert rendered_plaintext in manager.render("system", secret=rendered_plaintext)

    store = ResourceStore.open(tmp_path)
    try:
        payload = store.read_tx(
            lambda conn: conn.execute(
                "SELECT payload_json FROM config_sections "
                "WHERE file = 'prompts' AND section = 'global_declarations'"
            ).fetchone()[0]
        )
        export_path = tmp_path / "export.json"
        store.export("json", export_path, dry_run=False)
        exported = orjson.dumps(orjson.loads(export_path.read_bytes())).decode()
    finally:
        store.close()

    assert body_plaintext not in payload
    assert rendered_plaintext not in payload
    assert body_plaintext not in exported
    assert rendered_plaintext not in exported
