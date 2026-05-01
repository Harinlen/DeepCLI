"""Tests for user-defined tool loader."""

from __future__ import annotations

import textwrap
from pathlib import Path


from daemon.extensions.tools.base import PermissionLevel
from daemon.extensions.tools.user_loader import (
    _find_tool_classes,
    _import_module_from_path,
    _is_safe_path,
    load_user_tools,
)


class TestIsSafePath:
    """Tests for symlink escape detection."""

    def test_normal_file_is_safe(self, tmp_path: Path) -> None:
        """Regular file inside directory is safe."""
        f = tmp_path / "tool.py"
        f.touch()
        assert _is_safe_path(f, tmp_path) is True

    def test_symlink_inside_dir_is_safe(self, tmp_path: Path) -> None:
        """Symlink pointing to another file in the same directory is safe."""
        target = tmp_path / "real.py"
        target.touch()
        link = tmp_path / "link.py"
        link.symlink_to(target)
        assert _is_safe_path(link, tmp_path) is True

    def test_symlink_escape_is_rejected(self, tmp_path: Path) -> None:
        """Symlink pointing outside the tools directory is rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil.py"
        target.touch()

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        link = tools_dir / "evil_link.py"
        link.symlink_to(target)
        assert _is_safe_path(link, tools_dir) is False

    def test_nonexistent_file_is_rejected(self, tmp_path: Path) -> None:
        """File that doesn't exist is rejected."""
        assert _is_safe_path(tmp_path / "nope.py", tmp_path) is False


class TestImportModuleFromPath:
    """Tests for dynamic module import."""

    def test_valid_module(self, tmp_path: Path) -> None:
        """A valid Python file imports successfully."""
        f = tmp_path / "valid.py"
        f.write_text("X = 42\n")
        mod = _import_module_from_path(f)
        assert mod is not None
        assert mod.X == 42  # type: ignore[attr-defined]

    def test_syntax_error_returns_none(self, tmp_path: Path) -> None:
        """A file with a syntax error returns None."""
        f = tmp_path / "broken.py"
        f.write_text("this is not valid python !!!\n")
        mod = _import_module_from_path(f)
        assert mod is None

    def test_import_error_returns_none(self, tmp_path: Path) -> None:
        """A file that raises ImportError returns None."""
        f = tmp_path / "bad_import.py"
        f.write_text("import nonexistent_module_xyz\n")
        mod = _import_module_from_path(f)
        assert mod is None


class TestFindToolClasses:
    """Tests for Tool subclass discovery within a module."""

    def test_finds_tool_subclass(self, tmp_path: Path) -> None:
        """Discovers a Tool subclass defined in the module."""
        f = tmp_path / "my_tool.py"
        f.write_text(
            textwrap.dedent("""\
            from typing import Any
            from pydantic import BaseModel
            from daemon.extensions.tools.base import Tool, ToolContext, ToolResult, PermissionLevel

            class MyTool(Tool):
                name = "my_tool"
                description = "A test tool"
                permission_level = PermissionLevel.NONE
                class Input(BaseModel):
                    x: str
                async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
                    return ToolResult(output="ok")
            """)
        )
        mod = _import_module_from_path(f)
        assert mod is not None
        classes = _find_tool_classes(mod)
        assert len(classes) == 1
        assert classes[0].__name__ == "MyTool"

    def test_ignores_base_tool_class(self, tmp_path: Path) -> None:
        """The Tool ABC itself is not returned."""
        f = tmp_path / "reimport.py"
        f.write_text("from daemon.extensions.tools.base import Tool\n")
        mod = _import_module_from_path(f)
        assert mod is not None
        classes = _find_tool_classes(mod)
        assert len(classes) == 0

    def test_ignores_reimported_tools(self, tmp_path: Path) -> None:
        """Tool subclasses imported (not defined) in the module are ignored."""
        # First create a tool in one file
        tool_file = tmp_path / "real_tool.py"
        tool_file.write_text(
            textwrap.dedent("""\
            from typing import Any
            from pydantic import BaseModel
            from daemon.extensions.tools.base import Tool, ToolContext, ToolResult, PermissionLevel

            class RealTool(Tool):
                name = "real"
                description = "Real"
                permission_level = PermissionLevel.NONE
                class Input(BaseModel):
                    x: str
                async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
                    return ToolResult(output="ok")
            """)
        )
        # Import it in another file
        reexport_file = tmp_path / "reexport.py"
        reexport_file.write_text(
            f"import sys; sys.path.insert(0, '{tmp_path}')\n"
            "from mustang_user_tool_real_tool import RealTool  # noqa: F401\n"
        )
        # Load the real tool first so its module is available
        _import_module_from_path(tool_file)
        mod = _import_module_from_path(reexport_file)
        assert mod is not None
        # RealTool's __module__ is the original, not reexport
        classes = _find_tool_classes(mod)
        assert len(classes) == 0


def _write_tool_file(directory: Path, name: str, tool_name: str) -> Path:
    """Helper: write a valid tool .py file into directory."""
    f = directory / f"{name}.py"
    f.write_text(
        textwrap.dedent(f"""\
        from typing import Any
        from pydantic import BaseModel
        from daemon.extensions.tools.base import Tool, ToolContext, ToolResult, PermissionLevel

        class {name.title().replace("_", "")}Tool(Tool):
            name = "{tool_name}"
            description = "Test tool {tool_name}"
            permission_level = PermissionLevel.NONE
            class Input(BaseModel):
                msg: str
            async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
                return ToolResult(output=params.get("msg", ""))
        """)
    )
    return f


class TestLoadUserTools:
    """Integration tests for the full load_user_tools flow."""

    def test_loads_valid_tools(self, tmp_path: Path) -> None:
        """Valid tool files are discovered and instantiated."""
        _write_tool_file(tmp_path, "alpha", "alpha")
        _write_tool_file(tmp_path, "beta", "beta")
        tools = load_user_tools(tmp_path)
        names = {t.name for t in tools}
        assert names == {"alpha", "beta"}

    def test_nonexistent_dir_returns_empty(self) -> None:
        """A non-existent directory returns empty list."""
        tools = load_user_tools(Path("/this/does/not/exist"))
        assert tools == []

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        """An empty directory returns empty list."""
        tools = load_user_tools(tmp_path)
        assert tools == []

    def test_skips_underscore_files(self, tmp_path: Path) -> None:
        """Files starting with _ are skipped."""
        (tmp_path / "__init__.py").write_text("# skip me\n")
        (tmp_path / "_helper.py").write_text("X = 1\n")
        _write_tool_file(tmp_path, "good", "good")
        tools = load_user_tools(tmp_path)
        assert len(tools) == 1
        assert tools[0].name == "good"

    def test_skips_broken_files(self, tmp_path: Path) -> None:
        """Broken files are logged and skipped, good files still load."""
        _write_tool_file(tmp_path, "good", "good")
        (tmp_path / "broken.py").write_text("not valid python !!!\n")
        tools = load_user_tools(tmp_path)
        assert len(tools) == 1
        assert tools[0].name == "good"

    def test_skips_tools_with_empty_name(self, tmp_path: Path) -> None:
        """Tools with empty name are skipped."""
        f = tmp_path / "no_name.py"
        f.write_text(
            textwrap.dedent("""\
            from typing import Any
            from pydantic import BaseModel
            from daemon.extensions.tools.base import Tool, ToolContext, ToolResult, PermissionLevel

            class NoNameTool(Tool):
                name = ""
                description = "Missing name"
                permission_level = PermissionLevel.NONE
                class Input(BaseModel):
                    x: str
                async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
                    return ToolResult(output="")
            """)
        )
        tools = load_user_tools(tmp_path)
        assert len(tools) == 0

    def test_skips_symlink_escape(self, tmp_path: Path) -> None:
        """Symlinks that escape the tools directory are rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        _write_tool_file(outside, "evil", "evil")

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_tool_file(tools_dir, "good", "good")
        (tools_dir / "evil_link.py").symlink_to(outside / "evil.py")

        tools = load_user_tools(tools_dir)
        names = {t.name for t in tools}
        assert "good" in names
        assert "evil" not in names

    def test_tool_instances_are_functional(self, tmp_path: Path) -> None:
        """Loaded tools have correct attributes and schema."""
        _write_tool_file(tmp_path, "echo", "echo")
        tools = load_user_tools(tmp_path)
        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "echo"
        assert tool.permission_level == PermissionLevel.NONE
        schema = tool.input_schema()
        assert "properties" in schema
        assert "msg" in schema["properties"]
