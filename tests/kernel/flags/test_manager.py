from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, Field, ValidationError

from kernel.flags import FlagManager, KernelFlags


class ToolsFlags(BaseModel):
    bash: bool = True
    browser: bool = False
    bash_timeout: int = Field(120, ge=1, le=3600)


@pytest.fixture
def flags_path(tmp_path: Path) -> Path:
    return tmp_path / "flags.yaml"


async def test_initialize_without_file_uses_defaults(flags_path: Path) -> None:
    fm = FlagManager(path=flags_path)
    await fm.initialize()

    kernel_flags = fm.get_section("kernel")
    assert isinstance(kernel_flags, KernelFlags)
    assert kernel_flags.memory is True
    assert kernel_flags.tools is True
    assert not flags_path.exists()  # no auto-write on missing file


async def test_initialize_loads_existing_file(flags_path: Path) -> None:
    flags_path.write_text(
        yaml.safe_dump(
            {
                "kernel": {"memory": False, "tools": True},
                "tools": {"bash": False, "bash_timeout": 30},
            }
        )
    )
    fm = FlagManager(path=flags_path)
    await fm.initialize()
    tools = fm.register("tools", ToolsFlags)

    assert fm.get_section("kernel").memory is False
    assert tools.bash is False
    assert tools.browser is False  # default retained
    assert tools.bash_timeout == 30


async def test_register_returns_frozen_instance(flags_path: Path) -> None:
    """``register`` returns the Pydantic instance directly (not a callable),
    and the same instance is reachable via :meth:`get_section`."""
    fm = FlagManager(path=flags_path)
    await fm.initialize()
    tools = fm.register("tools", ToolsFlags)

    assert isinstance(tools, ToolsFlags)
    assert tools is fm.get_section("tools")
    # Default values when the file is absent.
    assert tools.bash is True
    assert tools.browser is False
    assert tools.bash_timeout == 120


async def test_register_conflict_raises(flags_path: Path) -> None:
    fm = FlagManager(path=flags_path)
    await fm.initialize()
    fm.register("tools", ToolsFlags)

    with pytest.raises(ValueError, match="already registered"):
        fm.register("tools", ToolsFlags)


async def test_register_validation_failure_raises(flags_path: Path) -> None:
    flags_path.write_text(yaml.safe_dump({"tools": {"bash_timeout": 999999}}))
    fm = FlagManager(path=flags_path)
    await fm.initialize()

    with pytest.raises(ValidationError):
        fm.register("tools", ToolsFlags)


async def test_register_unknown_fields_ignored(flags_path: Path) -> None:
    flags_path.write_text(yaml.safe_dump({"tools": {"bash": False, "unknown_field": 42}}))
    fm = FlagManager(path=flags_path)
    await fm.initialize()
    tools = fm.register("tools", ToolsFlags)

    assert tools.bash is False


async def test_list_all_returns_schemas_and_instances(flags_path: Path) -> None:
    fm = FlagManager(path=flags_path)
    await fm.initialize()
    fm.register("tools", ToolsFlags)

    all_sections = fm.list_all()
    assert set(all_sections.keys()) == {"kernel", "tools"}
    kernel_schema, kernel_instance = all_sections["kernel"]
    assert kernel_schema is KernelFlags
    assert isinstance(kernel_instance, KernelFlags)
    tools_schema, tools_instance = all_sections["tools"]
    assert tools_schema is ToolsFlags
    assert isinstance(tools_instance, ToolsFlags)


async def test_get_section_unknown_raises(flags_path: Path) -> None:
    fm = FlagManager(path=flags_path)
    await fm.initialize()

    with pytest.raises(KeyError):
        fm.get_section("nope")


async def test_initialize_is_idempotent(flags_path: Path) -> None:
    fm = FlagManager(path=flags_path)
    await fm.initialize()
    await fm.initialize()  # second call must not re-register "kernel"

    assert "kernel" in fm.list_all()


async def test_non_mapping_section_raises(flags_path: Path) -> None:
    flags_path.write_text(yaml.safe_dump({"tools": ["not", "a", "dict"]}))
    fm = FlagManager(path=flags_path)
    await fm.initialize()

    with pytest.raises(ValueError, match="must be a mapping"):
        fm.register("tools", ToolsFlags)


async def test_top_level_non_mapping_raises(flags_path: Path) -> None:
    flags_path.write_text(yaml.safe_dump(["just", "a", "list"]))
    fm = FlagManager(path=flags_path)

    with pytest.raises(ValueError, match="mapping at the top level"):
        await fm.initialize()
