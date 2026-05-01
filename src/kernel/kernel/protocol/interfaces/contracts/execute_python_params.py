"""Input to session/execute_python."""

from __future__ import annotations

from pydantic import BaseModel


class ExecutePythonParams(BaseModel):
    session_id: str
    code: str
    exclude_from_context: bool = False

