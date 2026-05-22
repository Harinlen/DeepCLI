from __future__ import annotations

import yaml

from kernel.core.storage import ResourceStore
from kernel.core.storage.import_export import apply_legacy_yaml_import


def test_imports_config_and_flags_yaml_into_sections(tmp_path) -> None:
    _write_yaml(tmp_path / "config" / "kernel.yaml", {"tools": {"enabled": True}})
    _write_yaml(
        tmp_path / "config" / "mcp.yaml",
        {"mcp": {"servers": {"local": {"command": "python"}}}},
    )
    _write_yaml(tmp_path / "config" / "flags.yaml", {"kernel": {"memory": False}})

    report = apply_legacy_yaml_import(tmp_path)

    assert set(report.imported) == {
        "legacy:kernel.yaml",
        "legacy:mcp.yaml",
        "legacy:flags.yaml",
    }
    store = ResourceStore.open(tmp_path)
    try:
        config_count = store.read_tx(
            lambda conn: conn.execute("SELECT COUNT(*) FROM config_sections").fetchone()[0]
        )
        flag_count = store.read_tx(
            lambda conn: conn.execute("SELECT COUNT(*) FROM flag_sections").fetchone()[0]
        )
        markers = store.read_tx(
            lambda conn: conn.execute("SELECT COUNT(*) FROM migration_sources").fetchone()[0]
        )
    finally:
        store.close()
    assert config_count == 2
    assert flag_count == 1
    assert markers == 3


def test_second_run_skips_unchanged_sources(tmp_path) -> None:
    _write_yaml(tmp_path / "config" / "kernel.yaml", {"tools": {"enabled": True}})
    first = apply_legacy_yaml_import(tmp_path)
    second = apply_legacy_yaml_import(tmp_path)

    assert first.imported == ("legacy:kernel.yaml",)
    assert second.skipped == ("legacy:kernel.yaml",)
    store = ResourceStore.open(tmp_path)
    try:
        events = store.read_tx(
            lambda conn: conn.execute("SELECT COUNT(*) FROM config_events").fetchone()[0]
        )
    finally:
        store.close()
    assert events == 1


def test_changed_yaml_after_import_does_not_overwrite_sqlite(tmp_path) -> None:
    path = tmp_path / "config" / "kernel.yaml"
    _write_yaml(path, {"tools": {"enabled": True}})
    apply_legacy_yaml_import(tmp_path)
    _write_yaml(path, {"tools": {"enabled": False}})

    report = apply_legacy_yaml_import(tmp_path)

    assert report.drift == ("legacy:kernel.yaml",)
    store = ResourceStore.open(tmp_path)
    try:
        payload = store.read_tx(
            lambda conn: conn.execute(
                "SELECT payload_json FROM config_sections WHERE section = 'tools'"
            ).fetchone()[0]
        )
        events = store.read_tx(
            lambda conn: conn.execute("SELECT COUNT(*) FROM config_events").fetchone()[0]
        )
    finally:
        store.close()
    assert '"enabled":true' in payload
    assert events == 1


def test_secret_name_reference_reports_manual_action(tmp_path) -> None:
    _write_yaml(
        tmp_path / "config" / "kernel.yaml",
        {"provider": {"api_key": "${secret:old-name}"}},
    )

    report = apply_legacy_yaml_import(tmp_path, dry_run=True)

    assert report.manual_actions == ("manual_secret_reference:old-name",)
    store = ResourceStore.open(tmp_path)
    try:
        rows = store.read_tx(
            lambda conn: conn.execute("SELECT COUNT(*) FROM config_sections").fetchone()[0]
        )
    finally:
        store.close()
    assert rows == 0


def _write_yaml(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True))
