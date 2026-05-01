"""Feature flags for the Tools subsystem.

Bound by ``ToolManager.startup`` via ``FlagManager.register("tools", ToolFlags)``.
A built-in tool with its flag set to ``False`` is not registered at
startup; re-enabling requires a kernel restart (FlagManager is
runtime-frozen by contract).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ToolFlags(BaseModel):
    """Enable/disable switches for built-in tools."""

    bash: bool = Field(True, description="Enable the Bash tool")
    powershell: bool = Field(True, description="Enable the PowerShell tool")
    file_read: bool = Field(True, description="Enable the FileRead tool")
    file_edit: bool = Field(True, description="Enable the FileEdit tool")
    file_write: bool = Field(True, description="Enable the FileWrite tool")
    glob: bool = Field(True, description="Enable the Glob tool")
    grep: bool = Field(True, description="Enable the Grep tool")
    repl: bool = Field(
        False,
        description="Enable the REPL batch-execution tool (hides primitive tools from LLM)",
    )

    def is_enabled(self, tool_name: str) -> bool:
        """Resolve a Tool's primary name to its flag.

        Unknown names default to ``True`` — MCP tools and user-installed
        tools are gated elsewhere (MCPManager, not here).
        """
        mapping = {
            "Bash": self.bash,
            "PowerShell": self.powershell,
            "FileRead": self.file_read,
            "FileEdit": self.file_edit,
            "FileWrite": self.file_write,
            "Glob": self.glob,
            "Grep": self.grep,
        }
        return mapping.get(tool_name, True)


__all__ = ["ToolFlags"]
