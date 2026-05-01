"""End-to-end tests for :class:`kernel.config.ConfigManager`.

Covers the invariants that subsystems rely on:

- layered loading (global + project + env + CLI) reaches bound
  sections
- first-bind-wins enforcement and schema-identity check
- readers work before *and* after an owner binds, and see live
  updates afterwards
- ``update`` writes only the global layer, preserves sibling
  sections, and strips default-valued fields
- validation / write failures leave memory and disk untouched
- ``changed`` signal payload is ``(old, new)``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, Field, ValidationError

from kernel.config import ConfigManager, MutableSection, ReadOnlySection


class ToolsConfig(BaseModel):
    bash: bool = True
    browser: bool = False
    bash_timeout: int = Field(120, ge=1, le=3600)


class McpConfig(BaseModel):
    enabled: bool = True
    url: str = "http://localhost"


class OtherToolsConfig(BaseModel):
    """Same fields as ToolsConfig but a distinct class — used to test
    the schema-identity check in ``_get_or_create_section``."""

    bash: bool = True
    bash_timeout: int = 120


# --------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------


@pytest.fixture
def global_dir(tmp_path: Path) -> Path:
    path = tmp_path / "global"
    path.mkdir()
    return path


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True))


async def _fresh(global_dir: Path, project_dir: Path) -> ConfigManager:
    cm = ConfigManager(
        global_dir=global_dir,
        project_dir=project_dir,
        cli_overrides=(),
    )
    await cm.startup()
    return cm


# --------------------------------------------------------------------
# startup + layered loading
# --------------------------------------------------------------------


async def test_startup_is_idempotent(global_dir: Path, project_dir: Path) -> None:
    cm = await _fresh(global_dir, project_dir)
    # A second call must not re-read or raise.
    await cm.startup()
    assert cm.get_section(file="config", section="tools", schema=ToolsConfig).get() == ToolsConfig()


async def test_bind_section_reads_layered_merge(global_dir: Path, project_dir: Path) -> None:
    _write_yaml(
        global_dir / "config.yaml",
        {"tools": {"bash_timeout": 60}},
    )
    _write_yaml(
        project_dir / "config.yaml",
        {"tools": {"browser": True}},
    )
    cm = await _fresh(global_dir, project_dir)

    tools = cm.bind_section(file="config", section="tools", schema=ToolsConfig)
    value = tools.get()
    assert value.bash_timeout == 60  # from global
    assert value.browser is True  # from project
    assert value.bash is True  # schema default preserved


async def test_defaults_when_no_files(global_dir: Path, project_dir: Path) -> None:
    cm = await _fresh(global_dir, project_dir)
    tools = cm.bind_section(file="config", section="tools", schema=ToolsConfig)
    assert tools.get() == ToolsConfig()


# --------------------------------------------------------------------
# first-bind-wins + schema identity
# --------------------------------------------------------------------


async def test_bind_section_second_call_raises(global_dir: Path, project_dir: Path) -> None:
    cm = await _fresh(global_dir, project_dir)
    cm.bind_section(file="config", section="tools", schema=ToolsConfig)
    with pytest.raises(ValueError, match="already bound"):
        cm.bind_section(file="config", section="tools", schema=ToolsConfig)


async def test_get_section_with_mismatched_schema_raises(
    global_dir: Path, project_dir: Path
) -> None:
    cm = await _fresh(global_dir, project_dir)
    cm.bind_section(file="config", section="tools", schema=ToolsConfig)
    with pytest.raises(ValueError, match="Schema mismatch"):
        cm.get_section(file="config", section="tools", schema=OtherToolsConfig)


async def test_reader_before_owner_is_allowed(global_dir: Path, project_dir: Path) -> None:
    """Readers may materialize a section before its owner binds; the
    owner's bind then reuses the exact same underlying state."""
    _write_yaml(
        global_dir / "config.yaml",
        {"tools": {"bash_timeout": 45}},
    )
    cm = await _fresh(global_dir, project_dir)

    reader = cm.get_section(file="config", section="tools", schema=ToolsConfig)
    assert reader.get().bash_timeout == 45

    owner = cm.bind_section(file="config", section="tools", schema=ToolsConfig)
    # Same underlying _Section means reader sees owner's writes.
    new_value = ToolsConfig(bash=True, browser=True, bash_timeout=90)
    await owner.update(new_value)
    assert reader.get().bash_timeout == 90
    assert reader.get().browser is True


async def test_owner_bind_schema_mismatch_with_reader(global_dir: Path, project_dir: Path) -> None:
    cm = await _fresh(global_dir, project_dir)
    cm.get_section(file="config", section="tools", schema=ToolsConfig)
    with pytest.raises(ValueError, match="Schema mismatch"):
        cm.bind_section(file="config", section="tools", schema=OtherToolsConfig)


# --------------------------------------------------------------------
# update: writes, persistence, signal
# --------------------------------------------------------------------


async def test_update_persists_to_global_file(global_dir: Path, project_dir: Path) -> None:
    cm = await _fresh(global_dir, project_dir)
    tools = cm.bind_section(file="config", section="tools", schema=ToolsConfig)
    await tools.update(ToolsConfig(bash=True, browser=True, bash_timeout=90))

    written = yaml.safe_load((global_dir / "config.yaml").read_text())
    # ``bash`` is at default (True) → stripped by exclude_defaults.
    # ``browser`` and ``bash_timeout`` are non-default → persisted.
    assert written == {"tools": {"browser": True, "bash_timeout": 90}}


async def test_update_preserves_sibling_sections(global_dir: Path, project_dir: Path) -> None:
    _write_yaml(
        global_dir / "config.yaml",
        {"mcp": {"enabled": False, "url": "http://remote"}},
    )
    cm = await _fresh(global_dir, project_dir)
    tools = cm.bind_section(file="config", section="tools", schema=ToolsConfig)
    await tools.update(ToolsConfig(bash_timeout=300))

    written = yaml.safe_load((global_dir / "config.yaml").read_text())
    assert written == {
        "mcp": {"enabled": False, "url": "http://remote"},
        "tools": {"bash_timeout": 300},
    }


async def test_update_drops_section_when_all_fields_default(
    global_dir: Path, project_dir: Path
) -> None:
    _write_yaml(
        global_dir / "config.yaml",
        {"tools": {"bash_timeout": 300}},
    )
    cm = await _fresh(global_dir, project_dir)
    tools = cm.bind_section(file="config", section="tools", schema=ToolsConfig)
    await tools.update(ToolsConfig())  # all defaults

    written = yaml.safe_load((global_dir / "config.yaml").read_text())
    # Empty section stripped entirely.
    assert written == {} or written is None


async def test_update_writes_only_global_not_project(global_dir: Path, project_dir: Path) -> None:
    _write_yaml(
        project_dir / "config.yaml",
        {"tools": {"browser": True}},
    )
    project_file = project_dir / "config.yaml"
    before = project_file.read_text()

    cm = await _fresh(global_dir, project_dir)
    tools = cm.bind_section(file="config", section="tools", schema=ToolsConfig)
    await tools.update(ToolsConfig(bash_timeout=999))

    # Project file is byte-identical — we never touch it.
    assert project_file.read_text() == before


async def test_update_emits_changed_signal(global_dir: Path, project_dir: Path) -> None:
    cm = await _fresh(global_dir, project_dir)
    tools = cm.bind_section(file="config", section="tools", schema=ToolsConfig)

    seen: list[tuple[ToolsConfig, ToolsConfig]] = []

    async def on_change(old: ToolsConfig, new: ToolsConfig) -> None:
        seen.append((old, new))

    tools.changed.connect(on_change)
    old_value = tools.get()
    new_value = ToolsConfig(bash_timeout=200)
    await tools.update(new_value)

    assert len(seen) == 1
    emitted_old, emitted_new = seen[0]
    assert emitted_old == old_value
    assert emitted_new.bash_timeout == 200


async def test_reader_changed_signal_fires_too(global_dir: Path, project_dir: Path) -> None:
    """A ReadOnlySection's ``changed`` proxy points at the same Signal
    as the owner's — readers subscribe through it."""
    cm = await _fresh(global_dir, project_dir)
    owner = cm.bind_section(file="config", section="tools", schema=ToolsConfig)
    reader = cm.get_section(file="config", section="tools", schema=ToolsConfig)

    hits: list[int] = []

    async def slot(_: ToolsConfig, new: ToolsConfig) -> None:
        hits.append(new.bash_timeout)

    reader.changed.connect(slot)
    await owner.update(ToolsConfig(bash_timeout=250))
    assert hits == [250]


# --------------------------------------------------------------------
# failure modes
# --------------------------------------------------------------------


async def test_validation_failure_leaves_state_untouched(
    global_dir: Path, project_dir: Path
) -> None:
    cm = await _fresh(global_dir, project_dir)
    tools = cm.bind_section(file="config", section="tools", schema=ToolsConfig)
    original = tools.get()

    hits: list[int] = []

    async def slot(_: ToolsConfig, __: ToolsConfig) -> None:
        hits.append(1)

    tools.changed.connect(slot)

    # bash_timeout=0 violates the ``ge=1`` constraint.
    with pytest.raises(ValidationError):
        await tools.update(ToolsConfig.model_construct(bash_timeout=0))

    # Memory unchanged.
    assert tools.get() == original
    # Signal not fired.
    assert hits == []
    # Disk not written.
    assert not (global_dir / "config.yaml").exists()


async def test_slot_exception_does_not_break_update(global_dir: Path, project_dir: Path) -> None:
    cm = await _fresh(global_dir, project_dir)
    tools = cm.bind_section(file="config", section="tools", schema=ToolsConfig)

    async def bad(_: ToolsConfig, __: ToolsConfig) -> None:
        raise RuntimeError("boom")

    tools.changed.connect(bad)
    # update itself must return success; disk and memory already moved.
    await tools.update(ToolsConfig(bash_timeout=111))

    assert tools.get().bash_timeout == 111
    written = yaml.safe_load((global_dir / "config.yaml").read_text())
    assert written == {"tools": {"bash_timeout": 111}}


async def test_invalid_raw_yaml_section_raises_on_bind(global_dir: Path, project_dir: Path) -> None:
    _write_yaml(
        global_dir / "config.yaml",
        {"tools": "not-a-mapping"},
    )
    cm = await _fresh(global_dir, project_dir)
    with pytest.raises(ValueError, match="must be a mapping"):
        cm.bind_section(file="config", section="tools", schema=ToolsConfig)


# --------------------------------------------------------------------
# multi-file / multi-section isolation
# --------------------------------------------------------------------


async def test_separate_files_write_to_separate_paths(global_dir: Path, project_dir: Path) -> None:
    cm = await _fresh(global_dir, project_dir)
    tools = cm.bind_section(file="config", section="tools", schema=ToolsConfig)
    mcp = cm.bind_section(file="mcp", section="server", schema=McpConfig)

    await tools.update(ToolsConfig(bash_timeout=77))
    await mcp.update(McpConfig(enabled=False, url="http://other"))

    assert (global_dir / "config.yaml").exists()
    assert (global_dir / "mcp.yaml").exists()
    # mcp.yaml does not contain tools and vice versa.
    assert "tools" not in yaml.safe_load((global_dir / "mcp.yaml").read_text())
    assert "server" not in yaml.safe_load((global_dir / "config.yaml").read_text())


async def test_wrappers_are_correct_types(global_dir: Path, project_dir: Path) -> None:
    cm = await _fresh(global_dir, project_dir)
    owner = cm.bind_section(file="config", section="tools", schema=ToolsConfig)
    reader = cm.get_section(file="config", section="tools", schema=ToolsConfig)
    assert isinstance(owner, MutableSection)
    assert isinstance(reader, ReadOnlySection)
    # The reader class does not expose ``update``.
    assert not hasattr(reader, "update")
