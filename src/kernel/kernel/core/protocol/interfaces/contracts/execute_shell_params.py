"""Input to session/execute_shell."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ExecuteShellParams(BaseModel):
    session_id: str
    command: str
    exclude_from_context: bool = False
    shell: Literal["auto", "bash", "sh", "pwsh", "powershell", "cmd"] = "auto"

